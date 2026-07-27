#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# ===== DEFAULTS =====
DEFAULT_APP = "my-default-app"
DEFAULT_TEAM = None
DEFAULT_REPO = None
DEFAULT_BRANCH = "master"
DEFAULT_ENV_FILE = "config.env"
DEFAULT_EMAIL = ""
DEFAULT_API_KEY = ""
DEFAULT_REGION = "eu"
SETTINGS_FILE = ".hk.json"


parser = argparse.ArgumentParser()

parser.add_argument("-a", "--app")
parser.add_argument("-t", "--team")
parser.add_argument("-url", "--repo")
parser.add_argument("-b", "--branch")
parser.add_argument("-e", "--env", action="append")
parser.add_argument("-f", "--file")
parser.add_argument("-mail", "--email")
parser.add_argument("-api", "--api_key")
parser.add_argument(
    "-r",
    "--region",
    choices=["us", "eu"],
    default=DEFAULT_REGION,
    help="Heroku Common Runtime region (default: eu)",
)
parser.add_argument("-c", "--container", action="store_true")
parser.add_argument("-d", "--deploy", action="store_true")

args = parser.parse_args()

APP = args.app if args.app else DEFAULT_APP
TEAM = args.team if args.team else DEFAULT_TEAM
REPO = args.repo if args.repo else DEFAULT_REPO
BRANCH = args.branch if args.branch else DEFAULT_BRANCH
ENV_VARS = args.env or []
ENV_FILE = args.file if args.file else DEFAULT_ENV_FILE
EMAIL = args.email if args.email else DEFAULT_EMAIL
API_KEY = args.api_key if args.api_key else DEFAULT_API_KEY
REGION = args.region
IS_CONTAINER = args.container or False
DEPLOY_ONLY = args.deploy


def load_settings():
    if Path(SETTINGS_FILE).exists():
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return None


if args.deploy:
    cfg = load_settings()
    if cfg:
        APP = cfg["app"]
        BRANCH = cfg["branch"]
        REGION = cfg.get("region", DEFAULT_REGION)
        IS_CONTAINER = cfg.get("container", IS_CONTAINER)


def save_settings():
    data = {
        "app": APP,
        "branch": BRANCH,
        "region": REGION,
        "container": IS_CONTAINER,
    }
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f)


def delete_settings():
    if Path(SETTINGS_FILE).exists():
        Path(SETTINGS_FILE).unlink()


def get_app_dir():
    cwd = Path.cwd()
    if cwd.name == APP:
        return cwd
    return cwd / APP


def run(cmd):
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


def write_env():
    if not ENV_VARS:
        return

    app_dir = get_app_dir()
    app_dir.mkdir(exist_ok=True)

    env_path = app_dir / ENV_FILE
    existing = {}

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    existing[k] = v

    for item in ENV_VARS:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        existing[k.strip()] = v.strip()

    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")

    print(f"Updated {env_path}")


def is_termux():
    return "com.termux" in os.environ.get("PREFIX", "")


def command_exists(cmd):
    return (
        subprocess.run(
            f"command -v {cmd}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def install_heroku():
    if command_exists("heroku"):
        return

    print("Heroku CLI not found. Installing...")

    if is_termux():
        print("Detected Termux environment")

        if not command_exists("npm"):
            run("pkg install nodejs -y")

        run("npm install -g heroku")

    else:
        print("Detected Linux environment")

        # Try the official installer first.
        if command_exists("curl"):
            run("curl https://cli-assets.heroku.com/install.sh | sh")
        else:
            run("apt update && apt install curl -y")
            run("curl https://cli-assets.heroku.com/install.sh | sh")

    print("Heroku CLI installed successfully.")


def setup_auth():
    os.environ["HEROKU_API_KEY"] = API_KEY
    run("heroku auth:whoami")


def app_exists():
    result = subprocess.run(
        f"heroku apps:info -a {APP}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def create_app():
    run("rm -rf .venv venv env __pycache__")

    if app_exists():
        print(
            f"Heroku app '{APP}' already exists. "
            "Its existing region cannot be changed."
        )
        return

    command = f"heroku apps:create {APP} --region {REGION}"

    if TEAM:
        command += f" --team {TEAM}"

    if IS_CONTAINER:
        command += " --stack container"

    run(command)


def clone_repo():
    if not REPO:
        return

    cwd = Path.cwd()

    if cwd.name == APP:
        print("Already inside repo. Skipping clone.")
        return

    if Path(APP).exists():
        print("Repo exists. Skipping clone.")
        return

    run(f"git clone -b {BRANCH} {REPO} {APP}")


def set_config_vars():
    for var in ENV_VARS:
        run(f"heroku config:set {var} -a {APP}")


def print_app_url():
    result = subprocess.check_output(
        f"heroku apps:info -a {APP}",
        shell=True,
        text=True,
    )

    for line in result.splitlines():
        if "Web URL" in line:
            url = line.split(":", 1)[-1].strip()
            print(f"\n🌐 App URL: {url}\n")
            return


def deploy():
    cwd = Path.cwd().resolve()

    if cwd.name != APP:
        os.chdir(APP)

    if IS_CONTAINER:
        run(f"heroku stack:set container -a {APP}")
        run(f"heroku container:push web -a {APP}")
        run(f"heroku container:release web -a {APP}")
    else:
        remote_url = f"https://heroku:{API_KEY}@git.heroku.com/{APP}.git"
        run("git init")
        run("git add . -f")
        run('git commit -m "deploy" || true')
        run("git remote remove heroku || true")
        run(f"git remote add heroku {remote_url}")
        run(f"git push heroku {BRANCH}:master -f")

    os.chdir("..")


# ================= EXECUTION =================

if not DEPLOY_ONLY:
    install_heroku()
    setup_auth()
    create_app()
    clone_repo()
    write_env()
    set_config_vars()
    save_settings()

    print("Settings saved. Run 'hk -d' to deploy.")
    print_app_url()

else:
    setup_auth()
    deploy()
    delete_settings()

    print("Deploy completed.")
    print_app_url()
