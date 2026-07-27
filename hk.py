#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import termios
import threading
import tty
from pathlib import Path


DEFAULT_APP = None
DEFAULT_TEAM = None
DEFAULT_REPO = None
DEFAULT_BRANCH = "master"
DEFAULT_ENV_FILE = "config.env"
DEFAULT_REGION = "eu"

SETTINGS_FILE = ".hk.json"
AUTH_FILE = Path.home() / ".hk_auth.json"


parser = argparse.ArgumentParser()

parser.add_argument("-a", "--app")
parser.add_argument("-t", "--team")
parser.add_argument("-url", "--repo")
parser.add_argument("-b", "--branch")
parser.add_argument("-e", "--env", action="append")
parser.add_argument("-file", "--file")
parser.add_argument(
    "-r",
    "--region",
    choices=["us", "eu"],
    default=DEFAULT_REGION,
    help="Heroku Common Runtime region (default: eu)",
)
parser.add_argument("-c", "--container", action="store_true")
parser.add_argument("-d", "--deploy", action="store_true")

parser.add_argument("-l", "--logs", action="store_true")
parser.add_argument("-f", "--follow", action="store_true")
parser.add_argument("-n", "--lines", type=int, default=100)

parser.add_argument("command", nargs="?", help="login | logout")

args = parser.parse_args()

APP = args.app or DEFAULT_APP
TEAM = args.team or DEFAULT_TEAM
REPO = args.repo or DEFAULT_REPO
BRANCH = args.branch or DEFAULT_BRANCH
ENV_FILE = args.file or DEFAULT_ENV_FILE
ENV_VARS = args.env or []
REGION = args.region
IS_CONTAINER = args.container
DEPLOY_ONLY = args.deploy


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

    if "com.termux" in os.environ.get("PREFIX", ""):
        print("Installing Heroku in Termux...")

        if not command_exists("npm"):
            run("pkg install nodejs -y")

        run("npm install -g heroku")

    else:
        print("Installing Heroku in Linux...")

        if command_exists("curl"):
            run("curl https://cli-assets.heroku.com/install.sh | sh")
        else:
            run("apt update && apt install curl -y")
            run("curl https://cli-assets.heroku.com/install.sh | sh")

    print("Heroku CLI installed.")


# ------------------------------------------------


def run(cmd):
    process = subprocess.run(cmd, shell=True)
    if process.returncode != 0:
        sys.exit(process.returncode)


# ------------------------------------------------
# AUTH SYSTEM
# ------------------------------------------------


def save_auth(email, key):
    data = {"email": email, "api_key": key}

    with open(AUTH_FILE, "w") as file:
        json.dump(data, file)

    os.chmod(AUTH_FILE, 0o600)

    netrc = f"""
machine api.heroku.com
  login {email}
  password {key}
machine git.heroku.com
  login {email}
  password {key}
"""

    netrc_path = Path.home() / ".netrc"

    with open(netrc_path, "w") as file:
        file.write(netrc.strip())

    os.chmod(netrc_path, 0o600)

    print("Login saved")


def load_auth():
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text())

    return None


def login():
    email = input("Heroku Email: ").strip()
    key = input("Heroku API Key: ").strip()

    if not email or not key:
        print("Email/API required")
        sys.exit(1)

    save_auth(email, key)


def logout():
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()

    netrc = Path.home() / ".netrc"
    if netrc.exists():
        netrc.unlink()

    print("Logged out")


def setup_auth():
    auth = load_auth()

    if not auth:
        print("Run: hk login")
        sys.exit(1)

    os.environ["HEROKU_API_KEY"] = auth["api_key"]


# ------------------------------------------------
# LOG STREAM
# ------------------------------------------------


def _keypress(stop):
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        sys.stdin.read(1)
        stop.set()
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


def stream_logs():
    print("\nStreaming logs (press key to stop)\n")

    stop = threading.Event()

    threading.Thread(target=_keypress, args=(stop,), daemon=True).start()

    process = subprocess.Popen(f"heroku logs -a {APP} --tail", shell=True)

    while process.poll() is None:
        if stop.is_set():
            process.terminate()
            break


# ------------------------------------------------
# GIT
# ------------------------------------------------


def ensure_git():
    email = subprocess.run(
        "git config user.email",
        shell=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    if not email:
        run('git config --global user.email "deploy@local"')
        run('git config --global user.name "deploy-bot"')


# ------------------------------------------------
# SETTINGS
# ------------------------------------------------


def save_settings():
    data = {
        "app": APP,
        "branch": BRANCH,
        "region": REGION,
        "container": IS_CONTAINER,
    }

    with open(SETTINGS_FILE, "w") as file:
        json.dump(data, file)


def load_settings():
    if Path(SETTINGS_FILE).exists():
        return json.loads(Path(SETTINGS_FILE).read_text())

    return None


def delete_settings():
    if Path(SETTINGS_FILE).exists():
        Path(SETTINGS_FILE).unlink()


# ------------------------------------------------
# ENV FILE
# ------------------------------------------------


def write_env():
    if not ENV_VARS:
        return

    env_path = Path(APP) / ENV_FILE

    env_path.parent.mkdir(exist_ok=True)

    existing = {}

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                existing[key] = value

    for item in ENV_VARS:
        if "=" not in item:
            continue

        key, value = item.split("=", 1)
        existing[key] = value

    with open(env_path, "w") as file:
        for key, value in existing.items():
            file.write(f"{key}={value}\n")


# ------------------------------------------------
# HEROKU
# ------------------------------------------------


def app_exists():
    return (
        subprocess.run(
            f"heroku apps:info -a {APP}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def create_app():
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

    if Path.cwd().name == APP:
        return

    if Path(APP).exists():
        return

    run(f"git clone -b {BRANCH} {REPO} {APP}")


# ------------------------------------------------
# DEPLOY
# ------------------------------------------------


def deploy():
    cwd = Path.cwd()

    if cwd.name != APP:
        os.chdir(APP)

    ensure_git()

    if IS_CONTAINER:
        run(f"heroku stack:set container -a {APP}")

    run("git add -A")
    run(f"git add -f {ENV_FILE} 2>/dev/null || true")

    run('echo "$(date)" > .deploy-ts')
    run("git add .deploy-ts")

    subprocess.run('git commit -m "deploy"', shell=True)

    run("git remote remove heroku 2>/dev/null || true")
    run(f"heroku git:remote -a {APP}")

    push = subprocess.run(
        "git push heroku HEAD:master -f",
        shell=True,
    )

    if push.returncode != 0:
        print("Deploy failed")
        return False

    print("Deploy pushed")

    return True


# ------------------------------------------------
# URL
# ------------------------------------------------


def print_url():
    try:
        output = subprocess.check_output(
            f"heroku apps:info -a {APP}",
            shell=True,
            text=True,
        )

        for line in output.splitlines():
            if "Web URL" in line:
                print("\n🌐 App URL:", line.split(":", 1)[1].strip(), "\n")
                return

    except Exception:
        pass


# ------------------------------------------------
# COMMANDS
# ------------------------------------------------


install_heroku()

if args.command == "login":
    login()
    sys.exit()

if args.command == "logout":
    logout()
    sys.exit()

# ------------------------------------------------

if args.logs:
    setup_auth()
    run(f"heroku logs -a {APP} --num {args.lines}")
    sys.exit()

# ------------------------------------------------

if not DEPLOY_ONLY:
    setup_auth()
    create_app()
    clone_repo()
    write_env()
    save_settings()

    print("Setup done → run hk -d")
    print_url()

# ------------------------------------------------

else:
    setup_auth()

    config = load_settings()

    if config:
        APP = config["app"]
        BRANCH = config["branch"]
        REGION = config.get("region", DEFAULT_REGION)
        IS_CONTAINER = config.get("container", IS_CONTAINER)

    ok = deploy()

    if ok:
        delete_settings()
        print_url()
        stream_logs()
