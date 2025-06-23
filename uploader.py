import os
import git
import shutil
import datetime

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("REPO_URL")

if not GITHUB_TOKEN or not REPO_URL:
    raise EnvironmentError("GITHUB_TOKEN oder REPO_URL ist nicht gesetzt!")

REPO_AUTH_URL = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
LOCAL_REPO_PATH = "/tmp/webhook-alarm-repo"

def clone_repo():
    if os.path.exists(LOCAL_REPO_PATH):
        shutil.rmtree(LOCAL_REPO_PATH)
    return git.Repo.clone_from(REPO_AUTH_URL, LOCAL_REPO_PATH, branch="main")

def git_upload(filename, local_source_path="."):
    repo = clone_repo()

    source_file = os.path.join(local_source_path, filename)
    target_file = os.path.join(LOCAL_REPO_PATH, filename)
    shutil.copy(source_file, target_file)

    repo.git.add(filename)
    commit_msg = f"Upload {filename} – {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    repo.index.commit(commit_msg)
    repo.remote(name='origin').push()

def git_download(filename, local_target_path="."):
    repo = clone_repo()

    source_file = os.path.join(LOCAL_REPO_PATH, filename)
    target_file = os.path.join(local_target_path, filename)

    if os.path.exists(source_file):
        shutil.copy(source_file, target_file)
    else:
        raise FileNotFoundError(f"{filename} existiert nicht im Git-Repo.")

def git_sync_all(files: list[str], local_path="."):
    repo = clone_repo()

    for filename in files:
        # Download
        source = os.path.join(LOCAL_REPO_PATH, filename)
        target = os.path.join(local_path, filename)
        if os.path.exists(source):
            shutil.copy(source, target)
        else:
            print(f"⚠️ {filename} fehlt im Repo – wird übersprungen.")
