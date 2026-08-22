# Agent Mafia

**Six personalities sit down at a table. Some are Mafia. All of them have
opinions.**

Watch AI agents bluff, accuse, investigate, protect, panic, and vote each other
out—or take a seat and discover how quickly six models decide you look suspicious.
Or play together with them!

![Animated terminal replay of eight angry AI models playing Mafia](docs/assets/everyone-angry.gif)

_A real game, replayed with eight short tempers and the Mafia's plotting exposed._

The terminal is only the first table. A proper visual game room—with faces,
secret roles, live accusations, and plenty of dramatic pointing—is coming next.

Agent Mafia is provider-agnostic. OpenRouter is the bundled shortcut, not a
dependency: every seat can point to a different OpenAI-compatible Chat
Completions endpoint, including local Ollama, and one seat can be you. Give each
seat a distinct voice and debating style with `communication_style`; the engine
still controls roles, visibility, and legal actions.

## Start a game

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

**Hosted example (OpenRouter)** — the bundled roster uses OpenRouter because
one key can reach several model families. Agent Mafia does not depend on it:
any OpenAI-compatible provider or local server can be used instead. To run this
example, set an [OpenRouter key](https://openrouter.ai/keys):

```bash
export OPENROUTER_API_KEY="your-key"

uv run mafia play

# or uv run mafia play examples/openrouter_one_of_each.example.toml if you want to have more fun
```

**Ollama** — start the daemon, then:

```bash
ollama pull qwen3:1.7b # the bigger the model the better the game.
uv run mafia play examples/ollama.toml
```

**You can play too** — run
`uv run mafia play examples/human.example.toml`. You get a secret role and make
speeches, votes, and Night choices from the terminal; the AI seats use the same
`OPENROUTER_API_KEY`.

`--step` pauses at every phase. `--seed -1` shuffles a new game.

## Choose your chaos

Every checked-in OpenRouter roster reads `OPENROUTER_API_KEY`; `.example.toml`
means it is safe to commit and customize, not that it must be copied first.

| You want… | Run this |
| --- | --- |
| Six moods at one table | `uv run mafia play examples/openrouter_one_of_each.example.toml` |
| Six cheap, fast OpenRouter models | `uv run mafia play` |
| Eight distinct characters on budget models | `uv run mafia play examples/openrouter_eight_players.example.toml` |
| Six model families through one OpenRouter key | `uv run mafia play examples/openrouter.example.toml` |
| Three Sonnets versus three GPTs who despise each other | `uv run mafia play examples/sonnet_vs_gpt.example.toml` |
| You versus five OpenRouter characters | `uv run mafia play examples/human.example.toml` |
| Five remote models and one local Ollama model | `uv run mafia play examples/mixed_local.example.toml` |
| Six characters through the OpenAI API | `uv run mafia play examples/openai.toml` |
| A completely local Ollama game | `uv run mafia play examples/ollama.toml` |

`mafia play` resolves its roster in this order: an explicit argument,
`MAFIA_CONFIG`, `./mafia.toml`, `$XDG_CONFIG_HOME/mafia/config.toml` (or
`~/.config/mafia/config.toml`), then the bundled default. `openai.toml` reads
`OPENAI_API_KEY`; `ollama.toml` needs a running Ollama daemon. Change names and
`communication_style` to build your own cast.

The `openrouter_but_everyone_*` files seat the same eight cheap OpenRouter
models and change only `default_communication_style`. Roles, votes, and
information stay legal; the table just becomes unbearable. The GIF at the top
is the angry table with every seat on that mood.

| The table is… | Run this |
| --- | --- |
| Angry, annoyed, and passive-aggressive | `uv run mafia play examples/openrouter_but_everyone_angry.example.toml` |
| Building one enormous conspiracy | `uv run mafia play examples/openrouter_but_everyone_is_paranoid.example.toml` |
| Holding a performance review | `uv run mafia play examples/openrouter_but_everyone_is_corporate.example.toml` |
| Turning murder into a growth lesson | `uv run mafia play examples/openrouter_but_everyone_is_inspirational.example.toml` |
| Too tired for another Night | `uv run mafia play examples/openrouter_but_everyone_is_exhausted.example.toml` |
| Each convinced they are the protagonist | `uv run mafia play examples/openrouter_but_everyone_is_the_protagonist.example.toml` |

Write your own rule with the same field, or give one seat a private style:

```toml
[game]
default_communication_style = "angry, annoyed and passive-aggressive communication only"

[[players]]
name = "Madame Suspicion"
provider = "openai_compatible"
model = "qwen3:1.7b"
base_url = "http://localhost:11434/v1"
communication_style = "Treat every accusation like a scandalous society rumor."
```

A game needs 6–12 players. Personalities change how agents talk, not what they
know or which actions are legal.

OpenRouter appears often in these examples because one key makes mixed-model
demos easy—not because the engine depends on it. Swap `base_url`, `model`, and
key settings per seat; see [Providers](#providers). Ready to cast your own
table? Jump to [Advanced](#advanced).

## How Mafia works

Everyone receives a secret role:

- **Mafia** know one another and choose a victim at Night.
- The **Detective** privately checks whether someone is Mafia.
- The **Doctor** protects one player from the Night attack.
- **Civilians** have only their memory, judgment, and ability to sound
  suspicious while claiming not to be suspicious.

By Day, the survivors debate and vote. By Night, special roles act in secret.
The Town wins by eliminating every Mafia player; the Mafia wins when they equal
or outnumber everyone else.

Games fail closed: permanent account/API errors abort immediately, a full table
outage trips a circuit breaker, and three consecutive no-elimination Days end
as a recorded stalemate.

The full rules—ties, runoffs, role counts, protection limits, who sees what—are
in [`docs/game-rules.md`](docs/game-rules.md).

## Your games are remembered

Every game lands in `mafia.db`.

```bash
uv run mafia runs list
uv run mafia runs search "I never trusted the weather forecaster"
uv run mafia replay RUN_ID --view public
uv run mafia stats --group-by model
```

Replay never calls a model. Search stays public unless you pass
`--include-private`. Stored snapshots mask inline API keys.

## Providers

There is no OpenRouter-specific code path. Automated seats speak the
OpenAI-compatible Chat Completions protocol: set `base_url`, `model`, and a key
for each seat, then mix hosts in one roster.

```toml
[game]
default_base_url = "https://api.groq.com/openai/v1"
default_api_key_env = "GROQ_API_KEY"
```

Optional `reasoning_effort` (`none` through `max`) is sent as the Chat
Completions field. Higher effort can cost more. Sessions resend the full
transcript each turn, so cost grows with the square of the game length.

The shipped examples cover [OpenRouter](https://openrouter.ai/keys), the OpenAI
API via `OPENAI_API_KEY`, and local Ollama. Model IDs and prices on OpenRouter
are in their [catalog](https://openrouter.ai/models).

## Commands

| Command | What it does |
| --- | --- |
| `mafia doctor [CONFIG]` | Checks a roster. `--live` sends one real request per model. |
| `mafia play [CONFIG]` | Plays one game. `--step` pauses at every phase. |
| `mafia replay RUN_ID` | Replays without calling a model. `--view public` hides secrets. |
| `mafia runs list` | Filters games by result, model, player, or date. |
| `mafia runs search QUERY` | Searches recorded dialogue and events. |
| `mafia stats` | Compares outcomes, failures, and token usage. |

## Development

```bash
uv sync --extra dev
uv run pre-commit install
uv run pre-commit run gitleaks
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src/mafia_game
uv run pytest
uv build
```

Database revisions use Alembic with SQLAlchemy Core metadata—there are no ORM
models, and runtime queries remain plain `sqlite3`. `RunDatabase` upgrades a
database automatically when it opens. When changing the schema, edit
`src/mafia_game/runs/schema.py`, generate a candidate revision, and review the
generated operations before committing them:

```bash
uv run alembic -x database=mafia.db revision --autogenerate -m "describe change"
uv run alembic -x database=mafia.db check
```

CI scans the Git history for secrets, then runs format, lint, types, tests, and
the package build on Python 3.11 and 3.14.

## Advanced

A roster is a TOML file: one `[game]` table and 6–12 `[[players]]` entries.
Unknown keys are rejected. Names must be unique (case-insensitive). At most
one seat may be `human`.

```toml
[game]
seed = -1
discussion_rounds = 2
speech_word_limit = 100
default_model = "qwen3:1.7b"
default_base_url = "http://localhost:11434/v1"
default_communication_style = "Stay brief and name a suspect."
max_concurrency = 2

[[players]]
name = "You"
provider = "human"

[[players]]
name = "Ada Lang"
provider = "openai_compatible"
model = "anthropic/claude-sonnet-5"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
reasoning_effort = "medium"
communication_style = "Play an immaculate diplomatic fixer."
```

`[game]` defaults fill any `openai_compatible` seat that omits the same field.
`default_api_key` and `default_api_key_env` are mutually exclusive; so are
per-player `api_key` and `api_key_env`. Prefer `*_env`. `base_url` must be an
absolute `http`/`https` URL with no credentials, query, or fragment.

### `[game]`

| Key | Default | Notes |
| --- | --- | --- |
| `seed` | unset | `-1` or omit for a random seed |
| `default_model` | — | Copied onto seats that omit `model` |
| `default_reasoning_effort` | — | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `default_communication_style` | — | Copied onto non-human seats |
| `default_base_url` | — | Required on each `openai_compatible` seat after defaults |
| `default_api_key` | — | Inline secret; snapshots mask it |
| `default_api_key_env` | — | Environment variable name |
| `max_concurrency` | `4` | 1–12 |
| `turn_timeout_seconds` | `120` | Up to 900 |
| `discussion_rounds` | `3` | 1–10 Day laps |
| `speech_word_limit` | `220` | 20–1000 |
| `defense_word_limit` | `120` | 20–500 |
| `private_planning_word_limit` | `120` | 20–500 |
| `repair_attempts` | `1` | 0 or 1 |
| `max_stalemate_days` | `3` | 1–20 consecutive no-elimination Days |

### `[[players]]`

| Key | Notes |
| --- | --- |
| `name` | Required, unique, at most 80 characters |
| `provider` | `openai_compatible`, `human`, or `fake` |
| `model` | Required for `openai_compatible`. Humans default to `terminal` |
| `reasoning_effort` | Optional; `openai_compatible` only |
| `communication_style` | Optional; at most 500 characters |
| `base_url` | Required for `openai_compatible` after defaults |
| `api_key` / `api_key_env` | Optional; mutually exclusive; `openai_compatible` only |

`fake` answers instantly with a legal action. It is for tests.

## License

[MIT](LICENSE) © 2026 Nikita Savinov
