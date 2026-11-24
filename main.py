"""LangBot adapter for a single owner's explicitly paired activity ledger."""
import asyncio
import logging
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from langbot_plugin.api.definition.plugin import BasePlugin
from langbot_plugin.api.entities.builtin.platform.message import MessageChain, Plain
from core import Ledger, summary, threshold_jobs

LOGGER = logging.getLogger('langbot.sight_sense')


def selector(value):
    return str(value.get('uuid') or value.get('id') or '') if isinstance(value, dict) else str(value or '')


class SightSense(BasePlugin):
    async def initialize(self):
        self.lock = asyncio.Lock()
        self.task = asyncio.create_task(self.scheduler(), name='sight-sense')

    def cfg(self):
        return self.get_config() or {}

    def ledger(self):
        path = Path(self.cfg().get('database_path') or '/app/data/sight-sense/activity.sqlite3')
        if not path.is_file():
            raise FileNotFoundError('视感数据库不可用，请检查采集器和共享目录挂载。')
        return Ledger(path)

    def matches(self, sender, bot):
        cfg = self.cfg()
        return bool(cfg.get('target_user_id') and selector(cfg.get('bot_uuid'))) and (
            str(sender) == str(cfg['target_user_id']) and str(bot) == selector(cfg['bot_uuid']))

    async def command(self, text):
        commands = {'!视感', '!视感 状态', '!视感 今日', '!视感 周报',
                    '!视感 暂停', '!视感 恢复', '!视感 清空'}
        if text not in commands:
            return None
        async with self.lock:
            try:
                return await asyncio.to_thread(self.handle_command, text)
            except Exception:
                LOGGER.exception('SightSense command failed')
                return '视感操作失败，请检查共享数据库和插件配置；未确认成功。'

    def handle_command(self, text):
        if text == '!视感':
            return '视感：!视感 状态 / 今日 / 周报 / 暂停 / 恢复 / 清空。仅记录前台活跃时长；画像分析待定。'
        ledger = self.ledger()
        if text in ('!视感 暂停', '!视感 恢复'):
            paused = text.endswith('暂停')
            ledger.set('paused', paused)
            return '视感已暂停采集、自动推送和上下文读取。' if paused else '视感已恢复；采集器需保持运行，插件启用后才会自动推送。'
        if text == '!视感 清空':
            ledger.clear()
            return '视感本地使用记录及推送标记已清空。已发出的聊天消息仍保留在聊天平台。'
        now = time.time()
        if text == '!视感 状态':
            heartbeat = ledger.get('heartbeat', 0)
            alive = heartbeat and 0 <= now - heartbeat <= 30
            return (f'视感：插件{"启用" if self.cfg().get("enabled", False) else "未启用"}；'
                    f'采集{"暂停" if ledger.get("paused", False) else "未暂停"}；'
                    f'采集器{"在线" if alive else "离线或尚未启动"}。画像分析未启用。')
        ledger.prune(now, 30)
        return summary(ledger, now, self.cfg().get('timezone') or 'Asia/Shanghai',
                       7 if text.endswith('周报') else 1)

    async def observation(self):
        async with self.lock:
            cfg = self.cfg()
            if not cfg.get('enabled', False) or not cfg.get('inject_context', True):
                return None
            def read():
                ledger = self.ledger()
                if ledger.get('paused', False):
                    return None
                now = time.time()
                ledger.prune(now, 30)
                return summary(ledger, now, cfg.get('timezone') or 'Asia/Shanghai')
            try:
                return await asyncio.to_thread(read)
            except Exception:
                LOGGER.exception('SightSense observation unavailable')
                return None

    async def tick(self):
        async with self.lock:
            cfg = dict(self.cfg())
            if not cfg.get('enabled', False) or not cfg.get('notify', True):
                return
            if not cfg.get('target_user_id') or not selector(cfg.get('bot_uuid')):
                return
            ZoneInfo(cfg.get('timezone') or 'Asia/Shanghai')
            ledger = self.ledger()
            if await asyncio.to_thread(ledger.get, 'paused', False):
                return
            now = time.time()
            await asyncio.to_thread(ledger.prune, now, 30)
            jobs = await asyncio.to_thread(threshold_jobs, ledger, now, cfg)
            generation = await asyncio.to_thread(ledger.get, 'generation', 0)
            for identity, text in jobs:
                # Recheck both local controls and live config before each send.
                fresh = self.cfg()
                if (not fresh.get('enabled', False) or not fresh.get('notify', True)
                        or fresh.get('target_user_id') != cfg.get('target_user_id')
                        or selector(fresh.get('bot_uuid')) != selector(cfg.get('bot_uuid'))
                        or await asyncio.to_thread(ledger.get, 'paused', False)
                        or await asyncio.to_thread(ledger.get, 'generation', 0) != generation):
                    return
                if not await asyncio.to_thread(ledger.reserve, identity, now):
                    continue
                try:
                    await asyncio.wait_for(self.send_message(selector(cfg['bot_uuid']), 'person',
                        str(cfg['target_user_id']), MessageChain([Plain(text=text)])), timeout=30)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.to_thread(ledger.delivered, identity, 'uncertain')
                    LOGGER.exception('SightSense delivery uncertain; no automatic duplicate retry')
                else:
                    await asyncio.to_thread(ledger.delivered, identity, 'sent')

    async def scheduler(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception('SightSense tick failed; next attempt in 30 seconds')
            await asyncio.sleep(30)

    async def shutdown(self):
        if getattr(self, 'task', None):
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
