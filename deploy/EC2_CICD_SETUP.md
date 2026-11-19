# EC2 CI/CD Setup

This project deploys using the same layout as the manual EC2 hosting:

- `backend`: FastAPI app running with Uvicorn on port `8000` inside Docker.
- `frontend`: React/Vite `dist` files copied to host Nginx at `/var/www/html`.
- Nginx proxies `/api` requests to the backend container.

## GitHub Self-Hosted Runner

This workflow assumes the self-hosted runner is already installed and running on the EC2 instance.

The deploy job runs on `runs-on: self-hosted`, so it executes directly on EC2. No SSH deployment secrets are required.

If your manually hosted project is not in the default path, add this repository variable in GitHub:

`Settings > Secrets and variables > Actions > Variables`

- `EC2_APP_DIR`: app checkout path on EC2. Defaults to `/opt/court-audio-transcript-system`.

## EC2 Requirements

Install Git, Docker, Node.js/npm, rsync, and Nginx on EC2. The user that runs the GitHub runner service must be able to run passwordless `sudo` (deployment uses `sudo -n` for Docker, copying files into `/var/www/html`, and reloading Nginx).

Port `80` must be open in the EC2 security group. Do not run another Nginx service on host port `80` unless you change the Compose port mapping.

The runner service user must have passwordless sudo for the commands used by deployment. For an `ubuntu` runner user, check with:

```bash
sudo -n true
```

If it fails, configure a narrow sudoers rule or run the GitHub runner under a user that already has non-interactive sudo.

## Production Env File

Create the backend production env file directly on EC2. Do not commit real secrets.

```bash
cd /opt/court-audio-transcript-system
cp backend/.env.example backend/.env
nano backend/.env
```

If you use a Google service account JSON file, put it in:

```bash
backend/credentials/
```

Then set `GOOGLE_APPLICATION_CREDENTIALS` in `backend/.env` to the container path, for example:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/service-account.json
```

## Host Nginx

Install the provided Nginx server config once:

```bash
sudo cp deploy/nginx/court-transcription.conf /etc/nginx/sites-available/court-transcription.conf
sudo ln -sfn /etc/nginx/sites-available/court-transcription.conf /etc/nginx/sites-enabled/court-transcription.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

This config serves the frontend from `/var/www/html` and proxies `/api` to the backend container on `127.0.0.1:8000`.

## Deployment

Push to `main`. The workflow will:

1. Run backend tests with `pytest -q`.
2. Build the React frontend with `npm run build`.
3. Run the deploy job on the EC2 self-hosted runner.
4. Pull the latest `main` branch in `EC2_APP_DIR`.
5. Build the backend Docker image and restart the backend container.
6. Build the frontend on EC2 and sync `frontend/dist` to `/var/www/html`.
7. Test Nginx, reload Nginx, and smoke-test `/api/health` plus the frontend page.
