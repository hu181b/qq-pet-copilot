"""Automatic release notices without real network, device or user data."""
import sys,unittest,json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import onnxruntime
import main
from PyQt6.QtWidgets import QWidget,QApplication
from PyQt6.QtCore import Qt
from src.update_checker import UpdateCheckResult,check_github_latest_release,_is_remote_newer


def result(tag='v1.5',ok=True,new=True):
    return UpdateCheckResult(ok,new,'1.4',tag.lstrip('v'),tag,'https://github.com/hu181b/qq-pet-copilot/releases/tag/'+tag,'','test')

class UpdateNoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def owner(self):
        return NS(_update_checking=True,_update_label=Mock(),_notified_update_tags=set(),_show_automatic_update_notice=Mock(return_value=True),_show_update_result_dialog=Mock())

    def test_new_version_prompts_once_per_session(self):
        owner=self.owner()
        for _ in range(3):main.MainWindow._on_update_result(owner,(False,result()))
        owner._show_automatic_update_notice.assert_called_once()
        self.assertFalse(owner._update_checking)
        main.MainWindow._on_update_result(owner,(False,result('v1.6')))
        self.assertEqual(owner._show_automatic_update_notice.call_count,2)

    def test_failure_and_current_version_are_silent(self):
        owner=self.owner()
        for r in [result(ok=False,new=False),result(new=False)]:main.MainWindow._on_update_result(owner,(False,r))
        owner._show_automatic_update_notice.assert_not_called()
        owner._show_update_result_dialog.assert_not_called()

    def test_manual_checks_still_show_results(self):
        owner=self.owner()
        main.MainWindow._on_update_result(owner,(True,result()))
        owner._show_update_result_dialog.assert_called_once()
        owner._show_automatic_update_notice.assert_not_called()

    def test_busy_notice_not_marked_delivered(self):
        owner=self.owner();owner._show_automatic_update_notice.return_value=False
        main.MainWindow._on_update_result(owner,(False,result()))
        self.assertEqual(owner._notified_update_tags,set())

    def test_native_notice_later_closes_without_browser(self):
        owner=QWidget();owner._update_notice=None
        with patch.object(main.QDesktopServices,'openUrl') as browser:
            self.assertTrue(main.MainWindow._show_automatic_update_notice(owner,result()))
            box=owner._update_notice
            self.assertEqual(box.windowModality(),Qt.WindowModality.NonModal)
            self.assertFalse(main.MainWindow._show_automatic_update_notice(owner,result()))
            next(b for b in box.buttons() if b.text()=='稍后').click()
            self.assertIsNone(owner._update_notice)
            browser.assert_not_called()
        owner.close()

    def test_native_notice_opens_release_only_on_click(self):
        owner=QWidget();owner._update_notice=None
        with patch.object(main.QDesktopServices,'openUrl') as browser:
            main.MainWindow._show_automatic_update_notice(owner,result())
            browser.assert_not_called()
            next(b for b in owner._update_notice.buttons() if b.text()=='打开发布页').click()
            browser.assert_called_once()
            self.assertEqual(browser.call_args.args[0].toString(),result().release_url)
        owner.close()

    def test_rate_limit_falls_back_to_public_release(self):
        from urllib.error import HTTPError
        from unittest.mock import MagicMock
        response=MagicMock();response.__enter__.return_value=response
        response.geturl.return_value='https://github.com/hu181b/qq-pet-copilot/releases/tag/v1.5'
        with patch('src.update_checker.urlopen',side_effect=[HTTPError('url',403,'limited',{},None),response]):
            r=check_github_latest_release('hu181b/qq-pet-copilot','1.4')
        self.assertTrue(r.ok and r.has_update)
        self.assertEqual(r.latest_tag,'v1.5')

    def test_fallback_rejects_other_sites_and_unresolved_redirect(self):
        from src.update_checker import _check_release_page
        from unittest.mock import MagicMock
        for url in ['https://example.com/releases/tag/v9','https://github.com/other/repo/releases/tag/v9','https://github.com/hu181b/qq-pet-copilot/releases/latest','https://github.com/hu181b/qq-pet-copilot/releases/tag/v2.0-beta']:
            response=MagicMock();response.__enter__.return_value=response;response.geturl.return_value=url
            with patch('src.update_checker.urlopen',return_value=response):
                self.assertIsNone(_check_release_page('hu181b/qq-pet-copilot','1.4',8))

    def test_release_response_and_version_comparison(self):
        response=Mock();response.read.return_value=json.dumps({'tag_name':'v1.5','html_url':result().release_url,'assets':[]}).encode()
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        with patch('src.update_checker.urlopen',return_value=context):
            self.assertTrue(check_github_latest_release('hu181b/qq-pet-copilot','1.4').has_update)
        self.assertTrue(_is_remote_newer('1.4-local.1','1.5'))
        self.assertTrue(_is_remote_newer('1.9','1.10'))
        self.assertFalse(_is_remote_newer('1.4','1.4.0'))
        self.assertFalse(_is_remote_newer('1.5','1.4'))

if __name__=='__main__':unittest.main()
