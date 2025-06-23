import os
import git
import shutil
import datetime

# Sicher: Token & URL aus Umgebungsvariablen laden
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("REPO_URL")

if not GITHUB_TOKEN or not REPO_URL:
    raise EnvironmentError("GITHUB_TOKEN oder REPO_URL ist nicht gesetzt!")

REPO_AUTH_URL = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
LOCAL_REPO_PATH = "/tmp/webhook-alarm-repo"

def git_upload(filename, local_source_path):
    # Lokales Repo ggf. löschen
    if os.path.exists(LOCAL_REPO_PATH):
        shutil.rmtree(LOCAL_REPO_PATH)

    # Repo klonen mit Auth
    repo = git.Repo.clone_from(REPO_AUTH_URL, LOCAL_REPO_PATH)

    # Datei kopieren
    full_path = os.path.join(LOCAL_REPO_PATH, filename)
    shutil.copy(os.path.join(local_source_path, filename), full_path)

    # Git-Commit und Push
    repo.git.add(filename)
    commit_msg = f"Upload {filename} – {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    repo.index.commit(commit_msg)
    origin = repo.remote(name='origin')
    origin.push()
