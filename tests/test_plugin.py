import asyncio
import importlib.util
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from core import Ledger
from main import SightSense


class PluginTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / 'activity.sqlite3'
        self.ledger = Ledger(path, create=True)
        now = time.time()
        for i in range(2):
            self.ledger.append(now - 120 + 60 * i, now - 60 + 60 * i, 'code.exe')
        self.plugin = SightSense()
        self.plugin.config = {'enabled': True, 'database_path': str(path), 'timezone': 'UTC',
                              'target_user_id': 'owner', 'bot_uuid': {'uuid': 'bot-a'},
                              'app_threshold_minutes': 1}
        self.plugin.lock = asyncio.Lock()
        self.plugin.send_message = AsyncMock()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_delivery_idempotence_and_restart(self):
        await self.plugin.tick()
        await self.plugin.tick()
        self.plugin.send_message.assert_awaited_once()
        args = self.plugin.send_message.await_args.args
        self.assertEqual(args[:3], ('bot-a', 'person', 'owner'))
        await self.plugin.tick()
        self.plugin.send_message.assert_awaited_once()

    async def test_delivery_uncertainty_is_not_retried(self):
        self.plugin.send_message.side_effect = TimeoutError('synthetic send failure')
        with self.assertLogs('langbot.sight_sense', level='ERROR'):
            await self.plugin.tick()
        await self.plugin.tick()
        self.plugin.send_message.assert_awaited_once()
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0], 'uncertain')

    async def test_disabled_and_paused_prevent_automatic_use(self):
        self.plugin.config['enabled'] = False
        await self.plugin.tick()
        self.assertIsNone(await self.plugin.observation())
        self.plugin.config['enabled'] = True
        self.assertIn('已暂停', await self.plugin.command('!视感 暂停'))
        await self.plugin.tick()
        self.assertIsNone(await self.plugin.observation())
        self.plugin.send_message.assert_not_awaited()
        await self.plugin.command('!视感 恢复')
        self.assertIn('code.exe', await self.plugin.observation())

    async def test_commands_clear_and_missing_mount(self):
        self.assertIsNone(await self.plugin.command('hello'))
        self.assertIn('code.exe', await self.plugin.command('!视感 今日'))
        self.assertIn('7 天', await self.plugin.command('!视感 周报'))
        self.assertIn('已清空', await self.plugin.command('!视感 清空'))
        self.assertNotIn('code.exe', await self.plugin.command('!视感 今日'))
        self.plugin.config['database_path'] += '.missing'
        with self.assertLogs('langbot.sight_sense', level='ERROR'):
            self.assertIn('操作失败', await self.plugin.command('!视感 今日'))

    async def test_listener_isolates_private_owner_and_bot(self):
        spec = importlib.util.spec_from_file_location('sight_listener',
            Path(__file__).resolve().parents[1] / 'components/event_listener/default.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        listener = module.SightListener()
        listener.plugin = self.plugin
        handlers = {}
        listener.handler = lambda event: lambda fn: handlers.setdefault(event.__name__, fn)
        await listener.initialize()
        handler = handlers['PromptPreProcessing']
        for session, bot, expected in [('group_owner', 'bot-a', 0), ('person_other', 'bot-a', 0),
                                       ('person_owner', 'bot-b', 0), ('person_owner', 'bot-a', 1)]:
            event = SimpleNamespace(session_name=session, default_prompt=[], prompt=[])
            await handler(SimpleNamespace(event=event, get_bot_uuid=AsyncMock(return_value=bot)))
            self.assertEqual(len(event.default_prompt), expected)
        self.assertFalse(self.plugin.matches('other', 'bot-a'))


if __name__ == '__main__':
    unittest.main()
