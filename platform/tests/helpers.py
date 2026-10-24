import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

os.environ.update(DB_PATH=":memory:", ADMIN_CHAT_ID="111", BRAND_NAME="x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class St:
    def __init__(self, d=None):
        self.d, self.state = d or {}, None

    async def get_data(self):
        return dict(self.d)

    async def update_data(self, **k):
        self.d.update(k)

    async def set_state(self, s):
        self.state = s

    async def clear(self):
        self.d, self.state = {}, None


def mkbot():
    b = MagicMock()
    for n in ("send_message", "send_photo", "send_document", "forward_message", "send_location"):
        setattr(b, n, AsyncMock())
    b.get_me = AsyncMock(return_value=MagicMock(username="TestBot"))
    return b


def msg(text=None, uid=5, **kw):
    m = MagicMock()
    m.text, m.from_user.id, m.from_user.full_name = text, uid, f"U{uid}"
    m.contact, m.photo, m.document, m.location = kw.get("contact"), kw.get("photo"), kw.get("document"), kw.get("location")
    m.chat.id, m.message_id = uid, 700 + uid
    for n in ("answer", "answer_photo", "answer_document", "edit_text", "edit_caption", "edit_reply_markup", "delete"):
        setattr(m, n, AsyncMock())
    m.bot = mkbot()
    return m


def cb(data, uid=5):
    c = MagicMock()
    c.data, c.from_user.id, c.from_user.full_name = data, uid, f"U{uid}"
    c.answer = AsyncMock()
    c.message = msg("orig", uid)
    c.message.photo = c.message.document = None
    c.message.caption = None
    c.bot = c.message.bot
    return c


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def cmd(args=None):
    return MagicMock(args=args)


def labels(kb_fn, uid):
    return [b.text for row in kb_fn(uid).keyboard for b in row]


def buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]
