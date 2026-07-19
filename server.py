"""
Run:  uvicorn server:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations
from fastapi import WebSocket
from game import Question, build_deck, score_round
import asyncio
import scraper

MAX_ROUNDS = 10
ROUND_SECONDS = 45

# --------------------------------------------------------------------------
# Room / player state
# --------------------------------------------------------------------------


class Player:
    def __init__(self, pid: str, name: str, letterboxd: str, color: str):
        self.id = pid
        self.name = name
        self.letterboxd = letterboxd.strip().lower()
        self.color = color
        self.score = 0
        self.ws: WebSocket | None = None

    @property
    def connected(self) -> bool:
        return self.ws is not None

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "letterboxd": self.letterboxd,
            "color": self.color,
            "score": self.score,
            "connected": self.connected,
        }


class Room:
    def __init__(self, code: str):
        self.code = code
        self.host_id: str | None = None
        self.players: dict[str, Player] = {}
        self.state = "LOBBY"
        self.deck: list[Question] = []
        self.round_i = -1
        self.votes: dict[str, str] = {}
        self.timer: asyncio.Task | None = None
        self.reaper: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.warnings: list[str] = []

    async def send(self, player: Player, msg: dict):
        if player.ws is not None:
            try:
                await player.ws.send_json(msg)
            except Exception:
                player.ws = None

    async def broadcast(self, msg: dict):
        await asyncio.gather(*(self.send(p, msg) for p in self.players.values()))

    def snapshot(self) -> dict:
        return {
            "type": "room_update",
            "room": self.code,
            "state": self.state,
            "host": self.host_id,
            "players": [p.public() for p in self.players.values()],
            "warnings": self.warnings,
        }

    async def start_game(self):
        self.state = "LOADING"
        self.warnings = []
        for p in self.players.values():
            p.score = 0
        await self.broadcast(self.snapshot())

        usernames = sorted({p.letterboxd for p in self.players.values()})

        async def progress(user, done, total, err):
            if err:
                self.warnings.append(err)
            await self.broadcast(
                {
                    "type": "loading",
                    "user": user,
                    "done": done,
                    "total": total,
                    "error": err,
                }
            )

        try:
            reviews = await scraper.fetch_all(usernames, progress)
        except Exception as e:
            self.state = "LOBBY"
            self.warnings.append(f"Scraping failed: {e}")
            await self.broadcast(self.snapshot())
            return

        for u in usernames:
            if not reviews.get(u):
                self.warnings.append(f"No written reviews found for '{u}'")

        self.deck = build_deck(reviews, max_rounds=MAX_ROUNDS)
        by_lb = {p.letterboxd: p.id for p in self.players.values()}
        for q in self.deck:
            q.author_pid = by_lb.get(q.review.username.lower())
        self.deck = [q for q in self.deck if q.author_pid in self.players]

        if not self.deck:
            self.state = "LOBBY"
            self.warnings.append(
                "No usable reviews found for anyone in this lobby — "
                "make sure the Letterboxd usernames are right and profiles are public."
            )
            await self.broadcast(self.snapshot())
            return

        self.round_i = -1
        await self.next_round()

    async def next_round(self):
        self.round_i += 1
        if self.round_i >= len(self.deck):
            await self.game_over()
            return
        self.state = "ROUND"
        self.votes = {}
        q = self.deck[self.round_i]
        await self.broadcast(
            {
                "type": "round_start",
                "round": self.round_i + 1,
                "total": len(self.deck),
                "film": q.review.film,
                "year": q.review.year,
                "rating": q.review.rating,
                "shared": q.shared,
                "text": q.review.text,
                "seconds": ROUND_SECONDS,
                "players": [p.public() for p in self.players.values()],
            }
        )
        if self.timer:
            self.timer.cancel()
        self.timer = asyncio.create_task(self._round_timeout())

    async def _round_timeout(self):
        try:
            await asyncio.sleep(ROUND_SECONDS)
            async with self.lock:
                if self.state == "ROUND":
                    await self.reveal()
        except asyncio.CancelledError:
            pass

    async def submit_vote(self, voter: str, guess: str):
        if (
            self.state != "ROUND"
            or voter not in self.players
            or guess not in self.players
        ):
            return
        self.votes[voter] = guess
        await self.broadcast({"type": "votes_update", "voted": list(self.votes.keys())})
        connected = [p.id for p in self.players.values() if p.connected]
        if all(pid in self.votes for pid in connected):
            await self.reveal()

    async def reveal(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.state = "REVEAL"
        q = self.deck[self.round_i]
        deltas = score_round(self.votes, q.author_pid)
        for pid, d in deltas.items():
            if pid in self.players:
                self.players[pid].score += d
        await self.broadcast(
            {
                "type": "reveal",
                "author": q.author_pid,
                "votes": self.votes,
                "deltas": deltas,
                "players": [p.public() for p in self.players.values()],
                "last_round": self.round_i + 1 >= len(self.deck),
            }
        )

    async def game_over(self):
        self.state = "GAME_OVER"
        ranked = sorted(self.players.values(), key=lambda p: -p.score)
        await self.broadcast(
            {
                "type": "game_over",
                "players": [p.public() for p in ranked],
            }
        )
