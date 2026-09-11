"""Review regressions: temporary files and mocked scheduling, no device actions."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import atomic_file, progress_store, settings, status_cache
from src.config import Config
from src.profiles import ProfileStore
from scenarios.runner import TaskQueueRunner, _QueueTask, QUEUE_POLL_INTERVAL


class ReviewRegression(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        path = self.root / 'data.json'
        path.write_text('original', encoding='utf-8')
        with patch.object(atomic_file.os, 'replace', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                atomic_file.atomic_write_text(path, 'replacement')
        self.assertEqual(path.read_text(), 'original')
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_nested_writers_have_independent_temporary_files(self):
        path = self.root / 'data.json'
        replace = atomic_file.os.replace
        def interleave(source, destination):
            with patch.object(atomic_file.os, 'replace', replace):
                atomic_file.atomic_write_text(path, 'inner')
            replace(source, destination)
        with patch.object(atomic_file.os, 'replace', side_effect=interleave):
            atomic_file.atomic_write_text(path, 'outer')
        self.assertEqual(path.read_text(), 'outer')
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_yaml_serialization_failure_does_not_truncate_config(self):
        path = self.root / 'config.yaml'
        path.write_text('# keep\nwork: {}\n', encoding='utf-8')
        with patch.object(settings, 'CONFIG_FILE', path):
            with patch.object(settings._yaml, 'dump', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    settings.save_raw({'work': {}})
        self.assertEqual(path.read_text(), '# keep\nwork: {}\n')

    def test_yaml_roundtrip_keeps_comments_and_reload_sees_new_value(self):
        from src.config import load_config
        path = self.root / 'config.yaml'
        path.write_text('# keep\nwork:\n  duration: 10分钟\n', encoding='utf-8')
        self.assertEqual(load_config(path).work.duration, '10分钟')
        with patch.object(settings, 'CONFIG_FILE', path):
            data = settings.load_raw()
            settings.set_value(data, 'work.duration', '45分钟')
            settings.save_raw(data)
        self.assertIn('# keep', path.read_text(encoding='utf-8'))
        self.assertEqual(load_config(path).work.duration, '45分钟')

    def test_invalid_status_entries_cannot_break_update_or_clear(self):
        path = self.root / 'status.json'
        with patch.object(status_cache, 'STATUS_CACHE_FILE', path):
            for value in (None, 3, [], 'broken'):
                with self.subTest(value=value):
                    path.write_text(json.dumps({'accounts': {'default': value}}))
                    self.assertEqual(status_cache.load_accounts(), {})
                    status_cache.clear_status_fields('energy')
                    status_cache.update_status(energy=80)
                    self.assertEqual(status_cache.load_accounts()['default']['energy'], 80)

    def test_invalid_progress_shape_is_backed_up(self):
        path = self.root / 'progress.json'
        path.write_text('[1, 2]')
        self.assertEqual(progress_store.read_raw(path), {})
        self.assertEqual((self.root / 'progress.corrupted.bak').read_text(), '[1, 2]')

    def test_read_failure_does_not_move_valid_progress(self):
        path = self.root / 'progress.json'
        path.write_text('{"learned": 4}')
        with patch.object(Path, 'read_text', side_effect=PermissionError):
            self.assertEqual(progress_store.read_raw(path), {})
        self.assertTrue(path.exists())
        self.assertFalse(list(self.root.glob('*.bak')))

    def test_profile_registry_schema_errors_are_explicit(self):
        store = ProfileStore(self.root)
        for data in ([], {}, {'profiles': [], 'active': 'default'},
                     {'profiles': {'default': 3}, 'active': 'default'},
                     {'profiles': {'default': '默认'}, 'active': []}):
            with self.subTest(data=data):
                store.file.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    store.read()

    def test_invalid_care_threshold_returns_validation_failure(self):
        for value in (None, 'bad', float('inf'), [], -1, 101):
            for key in ('care.energy_threshold', 'care.clean_threshold'):
                with self.subTest(value=value, key=key):
                    self.assertFalse(settings.validate_field(key, value)[0])

    def test_removed_main_task_cannot_block_remaining_task(self):
        runner = TaskQueueRunner.__new__(TaskQueueRunner)
        runner._last_cfg = Config()
        runner._last_cfg.tasks.order = 'school>work'
        tasks, order = {}, []
        runner._apply_tasks_config(tasks, order)
        work = tasks['work']
        runner._last_cfg.tasks.order = 'work>work'
        runner._apply_tasks_config(tasks, order)
        self.assertEqual(order, ['work'])
        self.assertEqual(set(tasks), {'work'})
        self.assertIs(tasks['work'], work)
        runner._main_pending_scen = Mock(return_value=None)
        runner.employed_window_active = Mock(return_value=False)
        runner._eligible = Mock(return_value=True)
        runner._school_due = Mock(return_value=True)
        self.assertEqual(runner._main_choice(tasks, {}), 'work')
        runner._school_due.assert_not_called()

    def test_removing_task_keeps_pending_activity_for_settlement(self):
        runner = TaskQueueRunner.__new__(TaskQueueRunner)
        runner._last_cfg = Config()
        tasks, order = {}, []
        runner._apply_tasks_config(tasks, order)
        runner.adventure = NS(pending=None)
        runner.school = NS(pending={'until': datetime.now() - timedelta(seconds=1),
                                    'desc': '学习'}, finish_pending=Mock())
        runner.hire_friend = NS(pending=None)
        runner.work = NS(pending=None)
        runner._last_cfg.tasks.order = 'work'
        runner._apply_tasks_config(tasks, order)
        self.assertEqual(runner._pending_finish_first(), 'finish')
        runner.school.finish_pending.assert_called_once()

    def test_side_task_wait_still_polls_hot_config(self):
        runner = TaskQueueRunner.__new__(TaskQueueRunner)
        runner._main_finished = Mock(return_value=True)
        task = _QueueTask('visit')
        task.next_at = datetime.now() + timedelta(hours=1)
        with patch('scenarios.runner.time.sleep') as sleep:
            self.assertTrue(runner._sleep_until_next({'visit': task}, ['visit']))
        self.assertLessEqual(sleep.call_args.args[0], QUEUE_POLL_INTERVAL)

    def test_missing_settlement_exit_does_not_count_or_drop_pending(self):
        from src.scenario import DeviceScenario
        scenario = DeviceScenario.__new__(DeviceScenario)
        finish = Mock()
        pending = {'until': datetime.now(), 'desc': '学习', 'end_name': 'school_end',
                   'in_name': 'school_in', 'on_finish': finish}
        scenario.pending = pending
        scenario.ensure_main_page = Mock()
        scenario.leave_home = Mock()
        scenario.snapshot = Mock(return_value=(None, None))
        scenario.see = Mock(side_effect=lambda name, *args: (1, 2) if name == 'school_end' else None)
        self.assertFalse(scenario.finish_pending())
        self.assertIs(scenario.pending, pending)
        self.assertGreater(pending['until'], datetime.now())
        finish.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
