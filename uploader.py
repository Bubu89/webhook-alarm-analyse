import git
import os
import shutil
from datetime import datetime

# Repo-Konfiguration
# Sichere Konfiguration über Umgebungsvariablen
TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("REPO_URL")
REPO_AUTH_URL = REPO_URL.replace("https://", f"https://{TOKEN}@")
LOCAL_REPO_PATH = "/tmp/webhook-alarm-repo"


def git_upload(filename, local_source_path):
    # Neues oder vorhandenes Repo klonen
    if os.path.exists(LOCAL_REPO_PATH):
        shutil.rmtree(LOCAL_REPO_PATH)
    repo = git.Repo.clone_from(REPO_URL, LOCAL_REPO_PATH)

    # Datei kopieren
    full_path = os.path.join(LOCAL_REPO_PATH, filename)
    shutil.copy(local_source_path, full_path)

    # Commit und Push
    repo.git.add(filename)
    repo.index.commit(f"Upload {filename} – {datetime.now().isoformat()}")
    origin = repo.remote(name='origin')
    origin.push()
