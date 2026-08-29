# retro-loop

A weekly, facilitated retrospective tool for a single project team.

## Run locally

```sh
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
```

## Docker

```sh
docker build -t retro-loop .
docker run --rm -p 8000:8000 -v retro-data:/data retro-loop
```

The SQLite file lives in the `retro-data` volume, so a rebuild keeps every row.

## Deploy (Fly.io)

Why Fly: the volume, the port and the health check all live in one committed `fly.toml`, and the container keeps its fixed port.

```sh
brew install flyctl
fly auth login
fly apps create retro-loop
fly volumes create data --region lhr --size 1 --yes
fly deploy --ha=false
fly status                              # one machine
fly volumes list                        # one volume
curl https://retro-loop.fly.dev/health  # {"status":"ok"}
```

Redeploy check: create a retro at https://retro-loop.fly.dev, run `fly deploy --ha=false` again, and the same code still joins.

- Redeploy = `fly deploy --ha=false`.
- Do not use `--local-only` on Apple Silicon (arm64 image, amd64 machines); the default remote builder is right.
- Region: `lhr` unless you pick another with `fly platform regions`; then change `primary_region` in `fly.toml` and `--region` above together.
