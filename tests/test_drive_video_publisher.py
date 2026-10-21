import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).resolve().parents[1] / 'scripts/post_drive_video_to_x.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 11, 15, tzinfo=p.ET)
        self.row = {'drive_file_id': 'a', 'filename': 'a.mp4', 'caption': 'A reviewed takeaway.',
                    'approved': True, 'evergreen': True}
        self.queue = {'videos': [self.row]}

    def test_only_reviewed_unused_and_boolean_approved(self):
        for approved in (False, 'true', None):
            self.row['approved'] = approved
            self.assertIsNone(p.choose(self.queue, {'posts': []}, self.now)[0])
        self.row['approved'] = True
        self.assertEqual(p.choose(self.queue, {'posts': []}, self.now)[0], self.row)
        ledger = {'posts': [{'status': 'posted', 'date': '2026-10-20', 'slot': 'old', 'drive_file_id': 'a'}]}
        self.assertIsNone(p.choose(self.queue, ledger, self.now)[0])

    def test_daily_limit_slots_pending_and_test(self):
        posts = [{'status': 'posted', 'date': '2026-10-21', 'slot': '2026-10-21-11', 'drive_file_id': 'b'}]
        self.assertIsNone(p.choose(self.queue, {'posts': posts}, self.now)[0])
        self.assertIsNotNone(p.choose(self.queue, {'posts': posts}, self.now, True)[0])
        posts.append({**posts[0], 'slot': 'manual', 'drive_file_id': 'c'})
        self.assertIsNone(p.choose(self.queue, {'posts': posts}, self.now, True)[0])
        with self.assertRaises(ValueError):
            p.choose(self.queue, {'posts': [{'status': 'pending'}]}, self.now)

    def test_eastern_dst(self):
        from datetime import timezone
        winter = datetime(2026, 12, 1, 16, tzinfo=timezone.utc).astimezone(p.ET)
        summer = datetime(2026, 7, 1, 15, tzinfo=timezone.utc).astimezone(p.ET)
        for now in (winter, summer):
            self.assertEqual(now.hour, 11)
            self.assertIsNotNone(p.choose(self.queue, {'posts': []}, now)[0])
        self.assertIsNone(p.choose(self.queue, {'posts': []}, self.now.replace(hour=12))[0])

    def test_encryption_refresh_and_callback_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'OAUTH_2_CLIENT_SECRET': 'test-secret-high-entropy-placeholder',
                'OAUTH_2_CLIENT_ID': 'client', 'PERSIST_TO_GIT': 'false'}), patch.object(p, 'ROOT', Path(tmp)), patch.object(p, 'STATE', Path(tmp) / 'state.enc'):
            p.save_state({'tokens': {'access_token': 'old-access', 'refresh_token': 'old-refresh'}, 'expires_at': 0})
            self.assertNotIn(b'old-refresh', p.STATE.read_bytes())
            with patch.object(p, 'exchange', return_value={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 7200}):
                self.assertEqual(p.access_token(), 'new-access')
            self.assertEqual(p.read_state()['tokens']['refresh_token'], 'rotated-refresh')
            p.save_state({'pending_auth': {'state': 'expected', 'created': p.time.time(), 'redirect': p.REDIRECT}})
            with patch.dict(os.environ, {'OAUTH_2_AUTHORIZATION_RESPONSE': p.REDIRECT + '?state=wrong&code=private'}), patch.object(p, 'exchange') as exchange:
                with self.assertRaisesRegex(ValueError, 'state mismatch'):
                    p.finish_auth()
                exchange.assert_not_called()

    def test_upload_chunks_and_processing(self):
        responses = [{'data': {'id': '123'}}, {}, {},
                     {'data': {'processing_info': {'state': 'pending', 'check_after_secs': 1}}},
                     {'data': {'processing_info': {'state': 'succeeded'}}}]
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'video.mp4'
            file.write_bytes(b'x' * (4*1024*1024 + 1))
            with patch.object(p, 'request', side_effect=responses) as call, patch.object(p.time, 'sleep'):
                self.assertEqual(p.upload_video(file, 'secret'), '123')
                self.assertEqual(call.call_count, 5)
                self.assertIn(b'\r\n\r\n0\r\n', call.call_args_list[1].kwargs['raw'])
                self.assertIn(b'\r\n\r\n1\r\n', call.call_args_list[2].kwargs['raw'])

    def test_callback_formats_and_errors_do_not_leak_secrets(self):
        url = p.REDIRECT + '?state=private-state&code=private-code'
        for value in (url, '  ' + url + '\n', '[' + url + '](' + url + ')', '<' + url + '>', '`' + url + '`'):
            self.assertEqual(p.parse_callback(value, p.REDIRECT)['code'], ['private-code'])
        for value in ('', p.REDIRECT, 'private-code', 'https://x.com/i/oauth2/authorize?state=private-state',
                      'http://other.invalid/?code=private-code&state=private-state',
                      p.REDIRECT + '?code=private-code', url + '&code=another-secret'):
            with self.assertRaises(ValueError) as error:
                p.parse_callback(value, p.REDIRECT)
            self.assertNotIn('private-code', str(error.exception))
            self.assertNotIn('private-state', str(error.exception))

    def test_uncertain_post_blocks_retry_and_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue, ledger = root / 'queue.json', root / 'posted.json'
            queue.write_text(json.dumps(self.queue))
            with patch.object(p, 'QUEUE', queue), patch.object(p, 'LEDGER', ledger), \
                    patch.object(p, 'access_token', return_value='token'), \
                    patch.object(p, 'read_state', return_value={'user_id': 'user'}), \
                    patch.object(p, 'download_video'), patch.object(p.subprocess, 'run'), \
                    patch.object(p, 'upload_video', return_value='media'), \
                    patch.object(p, 'checkpoint'), patch.object(p, 'summary'), \
                    patch.object(p, 'request', side_effect=[{'data': {'id': 'user'}}, TimeoutError('ambiguous')]) as request:
                with self.assertRaises(TimeoutError):
                    p.post(test_now=True, publish=True)
                self.assertEqual(json.loads(ledger.read_text())['posts'][0]['status'], 'pending')
                self.assertEqual(request.call_count, 2)
                with self.assertRaisesRegex(ValueError, 'uncertain outcome'):
                    p.post(test_now=True, publish=True)
                self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
