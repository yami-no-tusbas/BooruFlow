import math
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


# ============================================================
# CONFIGURATION
# ============================================================

DATABASE_FILE = os.getenv("GELBOORU_TAG_DB", "gelbooru_tags.db")
API_URL = "https://gelbooru.com/index.php"

# USER_ID : le nombre court fourni par Gelbooru.
USER_ID = os.getenv("GELBOORU_USER_ID", "")

# API_KEY : la longue clé fournie par Gelbooru.
# Renouvelle-la si une ancienne clé a été exposée.
API_KEY = os.getenv("GELBOORU_API_KEY", "")

USER_AGENT = (
    "GelbooruTagPipeline/1.0 "
    "(personal offline SQLite tag database)"
)

# Valeur déjà validée lors de la collecte précédente.
LIMIT = 100

# Nombre de requêtes HTTP simultanées.
# Commence à 3. Essaie 4 seulement si tu ne rencontres aucun HTTP 429.
MAX_WORKERS = 10

# Nombre maximal de PID en attente devant les workers.
# Une petite file maintient les connexions occupées sans lancer des milliers
# de requêtes d'avance.
DOWNLOAD_QUEUE_SIZE = MAX_WORKERS * 8
RESULT_QUEUE_SIZE = MAX_WORKERS * 8

# Une transaction SQLite toutes les 50 pages = environ 5 000 tags avec LIMIT=100.
COMMIT_EVERY_PAGES = 50

# Fréquence d'affichage de la progression.
PROGRESS_INTERVAL_SECONDS = 2.0

# Gestion des erreurs réseau.
REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 8
MAX_RETRY_DELAY_SECONDS = 60

# Estimation observée lorsque LIMIT=100.
# Le script s'arrête réellement à la première page vide ; cette valeur sert
# uniquement au pourcentage et à l'ETA.
REFERENCE_LAST_PID = 19950
REFERENCE_LIMIT = 100

# Les index secondaires ralentissent fortement l'import massif.
# Ils sont supprimés au démarrage puis recréés à la fin de la collecte.
DROP_SECONDARY_INDEXES_DURING_IMPORT = True

# Mets True uniquement pour reprendre malgré un état "completed=1".
FORCE_RESUME_COMPLETED_DATABASE = (
    os.getenv("GELBOORU_FORCE_UPDATE", "1").strip().lower()
    not in {"0", "false", "no"}
)


# ============================================================
# TYPES ET ÉTAT PARTAGÉ
# ============================================================

@dataclass
class DownloadResult:
    pid: int
    tags: Optional[list[dict]] = None
    response_size: int = 0
    duration: float = 0.0
    error: Optional[BaseException] = None


class SharedStats:
    def __init__(self, initial_next_pid: int, initial_tag_count: int) -> None:
        self.lock = threading.Lock()
        self.processed_pid = initial_next_pid - 1
        self.committed_next_pid = initial_next_pid
        self.pages_this_run = 0
        self.tags_this_run = 0
        self.total_tags_approx = initial_tag_count
        self.downloaded_bytes = 0
        self.last_commit_pages = 0
        self.last_commit_seconds = 0.0
        self.end_pid: Optional[int] = None
        self.status_message = "Démarrage"


thread_local = threading.local()


# ============================================================
# OUTILS GÉNÉRAUX
# ============================================================

def estimated_total_tags() -> int:
    return (REFERENCE_LAST_PID + 1) * REFERENCE_LIMIT


def estimated_total_pages() -> int:
    return math.ceil(estimated_total_tags() / LIMIT)


def format_duration(seconds: float) -> str:
    if seconds < 0 or not math.isfinite(seconds):
        return "--:--:--"

    value = int(seconds)
    hours = value // 3600
    minutes = (value % 3600) // 60
    remaining_seconds = value % 60
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def format_bytes(byte_count: int) -> str:
    value = float(byte_count)
    for suffix in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or suffix == "Go":
            return f"{value:.1f} {suffix}"
        value /= 1024
    return f"{value:.1f} Go"


def validate_configuration() -> None:
    if USER_ID == "METTRE_ICI_TON_USER_ID" or not USER_ID.strip():
        raise ValueError("Renseigne USER_ID avec le nombre court Gelbooru.")

    if API_KEY == "METTRE_ICI_TA_CLE_API" or not API_KEY.strip():
        raise ValueError("Renseigne API_KEY avec ta clé API Gelbooru.")

    if not USER_ID.isdigit():
        raise ValueError(
            "USER_ID devrait être uniquement numérique. "
            "USER_ID et API_KEY sont peut-être inversés."
        )

    if LIMIT < 1:
        raise ValueError("LIMIT doit être supérieur ou égal à 1.")

    if MAX_WORKERS < 1:
        raise ValueError("MAX_WORKERS doit être supérieur ou égal à 1.")

    if COMMIT_EVERY_PAGES < 1:
        raise ValueError("COMMIT_EVERY_PAGES doit être supérieur ou égal à 1.")


# ============================================================
# SQLITE
# ============================================================

def configure_sqlite(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA cache_size = -131072")  # environ 128 Mio
    connection.execute("PRAGMA busy_timeout = 30000")


def initialize_database(connection: sqlite3.Connection) -> None:
    configure_sqlite(connection)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            post_count INTEGER NOT NULL DEFAULT 0,
            category INTEGER NOT NULL DEFAULT 0,
            ambiguous INTEGER NOT NULL DEFAULT 0
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS scraper_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Compatibilité avec une ancienne version du script qui utilisait
    # une colonne nommée "name" au lieu de "key".
    state_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(scraper_state)"
        ).fetchall()
    }

    if "key" not in state_columns and "name" in state_columns:
        connection.execute(
            "ALTER TABLE scraper_state RENAME TO scraper_state_legacy"
        )
        connection.execute("""
            CREATE TABLE scraper_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        connection.execute("""
            INSERT OR REPLACE INTO scraper_state(key, value)
            SELECT name, value
            FROM scraper_state_legacy
        """)
        connection.execute(
            "DROP TABLE scraper_state_legacy"
        )

    connection.commit()


def create_secondary_indexes(connection: sqlite3.Connection) -> None:
    print("Création des index SQLite...")
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_name
        ON tags(name)
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_category
        ON tags(category)
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_tags_post_count
        ON tags(post_count)
    """)
    connection.commit()


def drop_secondary_indexes(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX IF EXISTS idx_tags_name")
    connection.execute("DROP INDEX IF EXISTS idx_tags_category")
    connection.execute("DROP INDEX IF EXISTS idx_tags_post_count")
    connection.commit()


def get_state(
    connection: sqlite3.Connection,
    key: str,
    default: Optional[str] = None,
) -> Optional[str]:
    row = connection.execute(
        "SELECT value FROM scraper_state WHERE key = ?",
        (key,),
    ).fetchone()

    return default if row is None else str(row[0])


def get_state_int(
    connection: sqlite3.Connection,
    key: str,
    default: int,
) -> int:
    value = get_state(connection, key, str(default))
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def set_state(
    connection: sqlite3.Connection,
    key: str,
    value: str | int,
) -> None:
    connection.execute("""
        INSERT INTO scraper_state(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, str(value)))


def count_tags(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) FROM tags").fetchone()
    return int(row[0])


def prepare_rows(tags: list[dict]) -> list[tuple[int, str, int, int, int]]:
    rows: list[tuple[int, str, int, int, int]] = []

    for tag in tags:
        if not isinstance(tag, dict):
            continue

        tag_id = tag.get("id")
        name = tag.get("name")

        if tag_id is None or not name:
            continue

        post_count = tag.get("count", tag.get("post_count", 0))
        category = tag.get("type", tag.get("category", 0))
        ambiguous = tag.get("ambiguous", 0)

        try:
            rows.append((
                int(tag_id),
                str(name),
                int(post_count or 0),
                int(category or 0),
                int(ambiguous or 0),
            ))
        except (TypeError, ValueError):
            print(f"Tag ignoré, données invalides : {tag!r}")

    return rows


# ============================================================
# HTTP / JSON
# ============================================================

def get_http_session() -> requests.Session:
    session = getattr(thread_local, "session", None)

    if session is None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        thread_local.session = session

    return session


def extract_tags(data) -> list[dict]:
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    success = data.get("success")
    if success is False or str(success).lower() == "false":
        raise RuntimeError(
            str(data.get("message", "Gelbooru a signalé une erreur."))
        )

    tags = data.get("tag", data.get("tags"))

    if isinstance(tags, list):
        return tags
    if isinstance(tags, dict):
        return [tags]
    return []


def build_parameters(pid: int) -> dict[str, str]:
    return {
        "page": "dapi",
        "s": "tag",
        "q": "index",
        "json": "1",
        "limit": str(LIMIT),
        "pid": str(pid),
        "api_key": API_KEY,
        "user_id": USER_ID,
    }


def download_page(pid: int) -> DownloadResult:
    retry_count = 0

    while True:
        started = time.monotonic()

        try:
            response = get_http_session().get(
                API_URL,
                params=build_parameters(pid),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            duration = time.monotonic() - started
            response_size = len(response.content)

            if response.status_code == 401:
                raise PermissionError(
                    "HTTP 401 : USER_ID ou API_KEY refusé."
                )

            if response.status_code == 403:
                raise PermissionError(
                    "HTTP 403 : accès interdit par Gelbooru."
                )

            if response.status_code == 429:
                raise requests.RequestException(
                    "HTTP 429 : trop de requêtes."
                )

            if response.status_code in (500, 502, 503, 504):
                raise requests.RequestException(
                    f"Erreur temporaire HTTP {response.status_code}."
                )

            response.raise_for_status()

            try:
                data = response.json()
            except requests.JSONDecodeError as error:
                preview = response.text[:300].replace("\n", " ")
                raise RuntimeError(
                    f"Réponse non JSON : {preview}"
                ) from error

            return DownloadResult(
                pid=pid,
                tags=extract_tags(data),
                response_size=response_size,
                duration=duration,
            )

        except PermissionError as error:
            return DownloadResult(pid=pid, error=error)

        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.RequestException,
            RuntimeError,
        ) as error:
            retry_count += 1

            if retry_count > MAX_RETRIES:
                return DownloadResult(
                    pid=pid,
                    error=RuntimeError(
                        f"PID {pid} abandonné après {MAX_RETRIES} "
                        f"tentatives : {error}"
                    ),
                )

            delay = min(MAX_RETRY_DELAY_SECONDS, 2 ** retry_count)
            print(
                f"PID {pid:,} | {error} | "
                f"nouvelle tentative dans {delay}s"
            )
            time.sleep(delay)


# ============================================================
# WORKERS DE TÉLÉCHARGEMENT
# ============================================================

def downloader_worker(
    pid_queue: queue.Queue,
    result_queue: queue.Queue,
    shutdown_event: threading.Event,
) -> None:
    while not shutdown_event.is_set():
        try:
            pid = pid_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        try:
            if pid is None or shutdown_event.is_set():
                return

            result = download_page(int(pid))

            while not shutdown_event.is_set():
                try:
                    result_queue.put(result, timeout=0.5)
                    break
                except queue.Full:
                    continue
        finally:
            pid_queue.task_done()


# ============================================================
# WRITER SQLITE
# ============================================================

def writer_worker(
    database_file: str,
    initial_next_pid: int,
    initial_tag_count: int,
    result_queue: queue.Queue,
    shutdown_event: threading.Event,
    stats: SharedStats,
    fatal_errors: list[BaseException],
) -> None:
    connection = sqlite3.connect(database_file, timeout=30)
    configure_sqlite(connection)

    expected_pid = initial_next_pid
    result_buffer: dict[int, DownloadResult] = {}
    pending_rows: list[tuple[int, str, int, int, int]] = []
    pending_pages = 0

    def commit_pending() -> None:
        nonlocal pending_rows, pending_pages

        if pending_pages == 0:
            return

        commit_started = time.monotonic()

        try:
            connection.execute("BEGIN IMMEDIATE")

            if pending_rows:
                connection.executemany("""
                    INSERT INTO tags (
                        id,
                        name,
                        post_count,
                        category,
                        ambiguous
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        post_count = excluded.post_count,
                        category = excluded.category,
                        ambiguous = excluded.ambiguous
                """, pending_rows)

            set_state(connection, "next_pid", expected_pid)
            set_state(connection, "limit", LIMIT)
            set_state(connection, "completed", 0)
            connection.commit()

        except Exception:
            connection.rollback()
            raise

        commit_duration = time.monotonic() - commit_started

        with stats.lock:
            stats.committed_next_pid = expected_pid
            stats.total_tags_approx += len(pending_rows)
            stats.last_commit_pages = pending_pages
            stats.last_commit_seconds = commit_duration
            stats.status_message = "Téléchargement et écriture"

        pending_rows = []
        pending_pages = 0

    try:
        while True:
            if shutdown_event.is_set() and result_queue.empty():
                break

            try:
                result: DownloadResult = result_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                result_buffer[result.pid] = result

                # Les pages sont validées exclusivement dans l'ordre des PID.
                while expected_pid in result_buffer:
                    current = result_buffer.pop(expected_pid)

                    if current.error is not None:
                        fatal_errors.append(current.error)
                        with stats.lock:
                            stats.status_message = f"Erreur au PID {expected_pid}"
                        shutdown_event.set()
                        return

                    tags = current.tags or []

                    if not tags:
                        commit_pending()

                        connection.execute("BEGIN IMMEDIATE")
                        set_state(connection, "next_pid", expected_pid)
                        set_state(connection, "completed", 1)
                        set_state(connection, "end_pid", expected_pid)
                        connection.commit()

                        with stats.lock:
                            stats.end_pid = expected_pid
                            stats.status_message = "Terminé"

                        shutdown_event.set()
                        return

                    rows = prepare_rows(tags)
                    pending_rows.extend(rows)
                    pending_pages += 1
                    expected_pid += 1

                    with stats.lock:
                        stats.processed_pid = expected_pid - 1
                        stats.pages_this_run += 1
                        stats.tags_this_run += len(rows)
                        stats.downloaded_bytes += current.response_size

                    if pending_pages >= COMMIT_EVERY_PAGES:
                        commit_pending()

            finally:
                result_queue.task_done()

    except BaseException as error:
        fatal_errors.append(error)
        shutdown_event.set()

    finally:
        try:
            # Les pages consécutives déjà traitées sont sauvegardées même lors
            # d'une interruption. Les pages hors ordre seront retéléchargées.
            commit_pending()
        except BaseException as error:
            fatal_errors.append(error)
            shutdown_event.set()

        connection.close()


# ============================================================
# AFFICHAGE DE PROGRESSION
# ============================================================

def display_progress(
    stats: SharedStats,
    run_start_time: float,
    scheduled_pid: int,
) -> None:
    with stats.lock:
        processed_pid = stats.processed_pid
        committed_next_pid = stats.committed_next_pid
        pages_this_run = stats.pages_this_run
        tags_this_run = stats.tags_this_run
        total_tags_approx = stats.total_tags_approx
        downloaded_bytes = stats.downloaded_bytes
        last_commit_pages = stats.last_commit_pages
        last_commit_seconds = stats.last_commit_seconds
        status_message = stats.status_message

    total_pages = estimated_total_pages()
    estimated_last_pid = total_pages - 1
    completed_pages = max(processed_pid + 1, 0)
    remaining_pages = max(total_pages - completed_pages, 0)
    progress = min(completed_pages / total_pages * 100, 100.0)

    elapsed = max(time.monotonic() - run_start_time, 0.001)
    pages_per_second = pages_this_run / elapsed
    eta_seconds = (
        remaining_pages / pages_per_second
        if pages_per_second > 0
        else float("inf")
    )

    print(
        f"[{progress:6.2f}%] "
        f"PID traité {processed_pid:,}/{estimated_last_pid:,} | "
        f"checkpoint {committed_next_pid:,} | "
        f"planifié jusqu'à {scheduled_pid - 1:,} | "
        f"{pages_per_second:.2f} pages/s | "
        f"{tags_this_run:,} tags cette session | "
        f"≈ {total_tags_approx:,} en base | "
        f"{format_bytes(downloaded_bytes)} | "
        f"dernier commit {last_commit_pages} pages/"
        f"{last_commit_seconds:.2f}s | "
        f"ETA {format_duration(eta_seconds)} | "
        f"{status_message}"
    )


# ============================================================
# PROGRAMME PRINCIPAL
# ============================================================

def main() -> None:
    validate_configuration()

    database_path = Path(DATABASE_FILE).resolve()

    setup_connection = sqlite3.connect(DATABASE_FILE, timeout=30)
    initialize_database(setup_connection)

    completed = get_state_int(setup_connection, "completed", 0)
    saved_limit = get_state_int(setup_connection, "limit", LIMIT)
    next_pid = get_state_int(setup_connection, "next_pid", 0)
    initial_tag_count = count_tags(setup_connection)

    if completed == 1 and not FORCE_RESUME_COMPLETED_DATABASE:
        end_pid = get_state_int(setup_connection, "end_pid", next_pid)
        setup_connection.close()
        print(f"La base est déjà marquée comme terminée au PID {end_pid:,}.")
        print(
            "Mets FORCE_RESUME_COMPLETED_DATABASE = True pour continuer "
            "à chercher de nouveaux tags."
        )
        input("\nAppuie sur Entrée pour fermer...")
        return

    if next_pid > 0 and saved_limit != LIMIT:
        setup_connection.close()
        raise ValueError(
            f"La base a été commencée avec LIMIT={saved_limit}, mais le "
            f"script utilise LIMIT={LIMIT}. Remets l'ancienne valeur ou "
            "utilise une nouvelle base."
        )

    if DROP_SECONDARY_INDEXES_DURING_IMPORT:
        print("Suppression temporaire des index secondaires...")
        drop_secondary_indexes(setup_connection)
    else:
        create_secondary_indexes(setup_connection)

    set_state(setup_connection, "limit", LIMIT)
    set_state(setup_connection, "completed", 0)
    setup_connection.commit()
    setup_connection.close()

    print(f"Base SQLite : {database_path}")
    print(f"Tags déjà présents : {initial_tag_count:,}")
    print(f"Reprise au PID : {next_pid:,}")
    print(f"Limite : {LIMIT} tags/page")
    print(f"Workers HTTP : {MAX_WORKERS}")
    print(f"Transaction toutes les {COMMIT_EVERY_PAGES} pages")
    print()

    pid_queue: queue.Queue = queue.Queue(maxsize=DOWNLOAD_QUEUE_SIZE)
    result_queue: queue.Queue = queue.Queue(maxsize=RESULT_QUEUE_SIZE)
    shutdown_event = threading.Event()
    stats = SharedStats(next_pid, initial_tag_count)
    fatal_errors: list[BaseException] = []

    writer = threading.Thread(
        target=writer_worker,
        name="SQLiteWriter",
        args=(
            DATABASE_FILE,
            next_pid,
            initial_tag_count,
            result_queue,
            shutdown_event,
            stats,
            fatal_errors,
        ),
        daemon=False,
    )

    downloaders = [
        threading.Thread(
            target=downloader_worker,
            name=f"Downloader-{index + 1}",
            args=(pid_queue, result_queue, shutdown_event),
            daemon=False,
        )
        for index in range(MAX_WORKERS)
    ]

    run_start_time = time.monotonic()
    next_progress_time = run_start_time + PROGRESS_INTERVAL_SECONDS
    scheduled_pid = next_pid
    interrupted = False

    writer.start()
    for worker in downloaders:
        worker.start()

    try:
        # Producteur : garde une fenêtre de PID en attente. Pendant ce temps,
        # le writer SQLite et les workers HTTP fonctionnent indépendamment.
        while not shutdown_event.is_set():
            try:
                pid_queue.put(scheduled_pid, timeout=0.25)
                scheduled_pid += 1
            except queue.Full:
                pass

            now = time.monotonic()
            if now >= next_progress_time:
                display_progress(stats, run_start_time, scheduled_pid)
                next_progress_time = now + PROGRESS_INTERVAL_SECONDS

    except KeyboardInterrupt:
        interrupted = True
        print("\nInterruption demandée : sauvegarde du dernier lot consécutif...")
        shutdown_event.set()

    finally:
        shutdown_event.set()

        for worker in downloaders:
            worker.join()

        writer.join()

    if fatal_errors:
        print("\nERREUR :", fatal_errors[0])

    final_connection = sqlite3.connect(DATABASE_FILE, timeout=30)
    configure_sqlite(final_connection)
    final_count = count_tags(final_connection)
    final_next_pid = get_state_int(final_connection, "next_pid", next_pid)
    final_completed = get_state_int(final_connection, "completed", 0)

    if final_completed == 1:
        create_secondary_indexes(final_connection)
        final_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        final_connection.commit()

    final_connection.close()

    display_progress(stats, run_start_time, scheduled_pid)
    print()
    print(f"Lignes présentes dans SQLite : {final_count:,}")
    print(f"Prochaine reprise : PID {final_next_pid:,}")

    if final_completed == 1:
        print("Collecte terminée et index recréés.")
    elif interrupted:
        print("Collecte interrompue proprement ; relance le script pour reprendre.")
    elif fatal_errors:
        print("Collecte arrêtée sur erreur ; la reprise reste sauvegardée.")

    input("\nAppuie sur Entrée pour fermer...")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nERREUR AU DÉMARRAGE :", error)
        input("\nAppuie sur Entrée pour fermer...")
