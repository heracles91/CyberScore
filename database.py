# database.py — Gestion de la base de données SQLite

import sqlite3
import config
from datetime import datetime

# Types d'événements à pré-charger au premier lancement
EVENT_TYPES_INIT = [
    # code, label, points, direction, one_shot
    ("MAIL_SUSPECT",            "Signalement mail suspect",                    10,  "+", 0),
    ("MAIL_CONFIRME",           "Mail malveillant confirmé (bonus)",           15,  "+", 0),
    ("PHISHING_RATE",           "Phishing simulé non cliqué",                 10,  "+", 0),
    ("QUIZ_REUSSI",             "Quiz cybersécurité réussi (>80%)",           15,  "+", 0),
    ("PORTAIL_SI",              "Demande via portail SI",                     10,  "+", 0),
    ("COFFRE_MDP",              "Preuve gestionnaire de mots de passe",       25,  "+", 1),
    ("FORMATION",               "Participation à une formation",              25,  "+", 0),
    ("PARRAINAGE",              "Parrainage d'un collègue",                   20,  "+", 0),
    ("MFA",                     "Activation authentification multi-facteurs", 25,  "+", 1),
    ("INCIDENT_CRITIQUE",       "Détection incident critique",                50,  "+", 0),
    ("VULN_INTERNE",            "Signalement vulnérabilité interne validée",  40,  "+", 0),
    ("COMPORTEMENT_EXEMPLAIRE", "Comportement exemplaire validé SI",          30,  "+", 0),
    ("SESSION_OUVERTE",         "Session Windows déverrouillée",             -20,  "-", 0),
    ("PHISHING_CLIC",           "Clic sur lien phishing simulé",             -30,  "-", 0),
    ("PHISHING_CREDS",          "Identifiants saisis sur phishing simulé",   -50,  "-", 0),
    ("MDP_EXPIRE",              "Mot de passe AD expiré >48h",               -15,  "-", 0),
    ("COMPTE_VERROUILLE",       "Compte AD verrouillé (tentatives échouées)",-10,  "-", 0),
    ("SESSION_NUIT",            "Session active nuit/weekend",               -10,  "-", 0),
    ("LOGICIEL_HORS_SI",        "Logiciel installé hors processus SI",       -25,  "-", 0),
    ("INACTIVITE",              "Malus inactivité mensuelle",                -10,  "-", 0),
]


def get_conn():
    """Retourne une connexion SQLite avec row_factory."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Crée les tables si elles n'existent pas et pré-charge les event_types."""
    conn = get_conn()
    try:
        c = conn.cursor()

        # Table settings (paramètres modifiables via l'interface)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Table users
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_login    TEXT    NOT NULL UNIQUE,
                nom         TEXT    NOT NULL,
                prenom      TEXT    NOT NULL,
                email       TEXT    NOT NULL,
                score_total INTEGER NOT NULL DEFAULT 0,
                niveau      TEXT    NOT NULL DEFAULT 'Débutant',
                actif       INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        # Table event_types
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_types (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                code           TEXT    NOT NULL UNIQUE,
                label          TEXT    NOT NULL,
                points         INTEGER NOT NULL,
                direction      TEXT    NOT NULL CHECK(direction IN ('+','-')),
                email_subject  TEXT,
                email_template TEXT    NOT NULL DEFAULT 'event_notification.html',
                one_shot       INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Table events
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                type_id    INTEGER NOT NULL REFERENCES event_types(id),
                points     INTEGER NOT NULL,
                raison     TEXT,
                created_by TEXT    NOT NULL,
                annule     INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        # Table notifications
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id  INTEGER NOT NULL REFERENCES events(id),
                user_id   INTEGER NOT NULL REFERENCES users(id),
                sent_at   TEXT,
                status    TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','error')),
                error_msg TEXT
            )
        """)

        # Table leaderboard_log (snapshot quotidien)
        c.execute("""
            CREATE TABLE IF NOT EXISTS leaderboard_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id),
                rang          INTEGER NOT NULL,
                score         INTEGER NOT NULL,
                niveau        TEXT    NOT NULL,
                snapshot_date TEXT    NOT NULL
            )
        """)

        # Pré-chargement des types d'événements (si table vide)
        c.execute("SELECT COUNT(*) FROM event_types")
        if c.fetchone()[0] == 0:
            for code, label, points, direction, one_shot in EVENT_TYPES_INIT:
                sujet = f"CyberScore — {label}"
                c.execute(
                    "INSERT INTO event_types (code, label, points, direction, email_subject, one_shot) VALUES (?,?,?,?,?,?)",
                    (code, label, points, direction, sujet, one_shot)
                )

        # ─ Quiz ─────────────────────────────────────────────────────────────────

        c.execute("""
            CREATE TABLE IF NOT EXISTS quizzes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT    NOT NULL,
                description    TEXT,
                date_limite    TEXT,
                seuil_reussite INTEGER NOT NULL DEFAULT 80,
                created_by     TEXT    NOT NULL,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                actif          INTEGER NOT NULL DEFAULT 1
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS quiz_questions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id       INTEGER NOT NULL REFERENCES quizzes(id),
                question_text TEXT    NOT NULL,
                ordre         INTEGER NOT NULL DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS quiz_choices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL REFERENCES quiz_questions(id),
                choice_text TEXT    NOT NULL,
                is_correct  INTEGER NOT NULL DEFAULT 0,
                ordre       INTEGER NOT NULL DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id        INTEGER NOT NULL REFERENCES quizzes(id),
                user_id        INTEGER NOT NULL REFERENCES users(id),
                token          TEXT    NOT NULL UNIQUE,
                sent_at        TEXT,
                started_at     TEXT,
                completed_at   TEXT,
                score_pct      INTEGER,
                nb_correct     INTEGER,
                nb_total       INTEGER,
                points_awarded INTEGER DEFAULT 0,
                status         TEXT    NOT NULL DEFAULT 'pending'
                               CHECK(status IN ('pending','completed','expired'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS quiz_attempt_answers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id  INTEGER NOT NULL REFERENCES quiz_attempts(id),
                question_id INTEGER NOT NULL REFERENCES quiz_questions(id),
                choice_id   INTEGER NOT NULL REFERENCES quiz_choices(id)
            )
        """)

        # ─ Formations ───────────────────────────────────────────────────────────

        c.execute("""
            CREATE TABLE IF NOT EXISTS formations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                description TEXT,
                mois        TEXT    NOT NULL,
                created_by  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                actif       INTEGER NOT NULL DEFAULT 1
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS formation_resources (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                formation_id  INTEGER NOT NULL REFERENCES formations(id),
                title         TEXT    NOT NULL,
                resource_type TEXT    NOT NULL CHECK(resource_type IN ('link','file')),
                url           TEXT,
                file_path     TEXT,
                file_name     TEXT,
                description   TEXT,
                ordre         INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)

        # ─── Migrations (ajout colonnes si absentes) ─────────────────────────
        for migration_sql in [
            "ALTER TABLE users ADD COLUMN is_tester INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE quiz_attempts ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE quiz_questions ADD COLUMN multiple_answers INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE event_types ADD COLUMN one_shot INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                c.execute(migration_sql)
            except sqlite3.OperationalError:
                pass  # Colonne déjà présente

        # ─── Migrations : nouveaux types d'événements (BDD existantes) ───────
        for code, label, points, direction, one_shot in EVENT_TYPES_INIT:
            try:
                sujet = f"CyberScore — {label}"
                c.execute(
                    "INSERT OR IGNORE INTO event_types (code, label, points, direction, email_subject, one_shot) VALUES (?,?,?,?,?,?)",
                    (code, label, points, direction, sujet, one_shot)
                )
            except sqlite3.OperationalError:
                pass

        # Marquer les types one-shot existants
        for code in ("COFFRE_MDP", "MFA"):
            c.execute("UPDATE event_types SET one_shot=1 WHERE code=? AND one_shot=0", (code,))

        # ─── Paramètres de scoring par défaut ────────────────────────────────
        for key, val in [
            ("monthly_cap_enabled", "1"),
            ("monthly_cap_points", "80"),
            ("decay_enabled", "0"),
            ("decay_points", "-10"),
        ]:
            existing = c.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone()
            if not existing:
                c.execute("INSERT INTO settings (key, value) VALUES (?,?)", (key, val))

        conn.commit()
        print("[DB] Base de données initialisée.")
    finally:
        conn.close()


# ─── Paramètres (settings) ────────────────────────────────────────────────────

def get_setting(key, default=None):
    """Retourne la valeur d'un paramètre stocké en BDD, ou default si absent."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    """Enregistre (ou met à jour) un paramètre en BDD."""
    conn = get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()
    finally:
        conn.close()


# ─── Gestion des types d'événements ──────────────────────────────────────────

def create_event_type(code, label, points, direction, one_shot=0):
    """Crée un nouveau type d'événement. Retourne (ok, error_msg)."""
    conn = get_conn()
    try:
        subject = f"CyberScore — {label}"
        conn.execute(
            "INSERT INTO event_types (code, label, points, direction, email_subject, one_shot) VALUES (?,?,?,?,?,?)",
            (code.strip().upper(), label.strip(), int(points), direction, subject, int(one_shot))
        )
        conn.commit()
        return True, None
    except sqlite3.IntegrityError as e:
        return False, f"Le code '{code}' existe déjà."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_event_type(type_id):
    """
    Supprime un type d'événement s'il n'est associé à aucun événement actif.
    Retourne (ok, error_msg).
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM events WHERE type_id=? AND annule=0", (type_id,)
        ).fetchone()
        if row[0] > 0:
            return False, f"Impossible de supprimer : {row[0]} événement(s) actif(s) utilisent ce type."
        conn.execute("DELETE FROM event_types WHERE id=?", (type_id,))
        conn.commit()
        return True, None
    finally:
        conn.close()


def update_event_type(type_id, code, label, points, direction, one_shot):
    """Met à jour un type d'événement. Retourne (ok, error_msg)."""
    conn = get_conn()
    try:
        subject = f"CyberScore — {label}"
        conn.execute("""
            UPDATE event_types
            SET code=?, label=?, points=?, direction=?, email_subject=?, one_shot=?
            WHERE id=?
        """, (code.strip().upper(), label.strip(), int(points), direction, subject, int(one_shot), type_id))
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, f"Le code '{code}' existe déjà pour un autre type."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def login_exists_in_db(ad_login, exclude_id=None):
    """Vérifie si un login AD existe déjà (pour la génération auto de login CSV)."""
    conn = get_conn()
    try:
        if exclude_id:
            row = conn.execute("SELECT id FROM users WHERE ad_login=? AND id!=?", (ad_login, exclude_id)).fetchone()
        else:
            row = conn.execute("SELECT id FROM users WHERE ad_login=?", (ad_login,)).fetchone()
        return row is not None
    finally:
        conn.close()


# ─── Utilisateurs ──────────────────────────────────────────────────────────────

def get_all_users(actif_only=False):
    conn = get_conn()
    try:
        q = "SELECT * FROM users"
        if actif_only:
            q += " WHERE actif=1"
        q += " ORDER BY score_total DESC"
        return [dict(r) for r in conn.execute(q).fetchall()]
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_login(ad_login):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE ad_login=?", (ad_login,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_user(ad_login, nom, prenom, email):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (ad_login, nom, prenom, email) VALUES (?,?,?,?)",
            (ad_login.strip().lower(), nom.strip().upper(), prenom.strip(), email.strip().lower())
        )
        conn.commit()
        return True, None
    except sqlite3.IntegrityError as e:
        return False, str(e)
    finally:
        conn.close()


def toggle_user_actif(user_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET actif = CASE WHEN actif=1 THEN 0 ELSE 1 END WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def toggle_user_tester(user_id):
    """Bascule le statut testeur d'un utilisateur."""
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET is_tester = CASE WHEN is_tester=1 THEN 0 ELSE 1 END WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_tester_users():
    """Retourne les utilisateurs actifs marqués comme testeurs."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM users WHERE is_tester=1 AND actif=1").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_users(query):
    """Recherche par nom, prénom ou login (pour l'autocomplete)."""
    conn = get_conn()
    try:
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT id, ad_login, nom, prenom, score_total, niveau FROM users WHERE actif=1 AND (nom LIKE ? OR prenom LIKE ? OR ad_login LIKE ?) ORDER BY nom LIMIT 20",
            (like, like, like)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Types d'événements ────────────────────────────────────────────────────────

def get_all_event_types():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM event_types ORDER BY direction DESC, label").fetchall()]
    finally:
        conn.close()


def get_event_type_by_id(type_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM event_types WHERE id=?", (type_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_event_type_by_code(code):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM event_types WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ─── Événements ───────────────────────────────────────────────────────────────

def insert_event(user_id, type_id, points, raison, created_by):
    """Insère un événement et retourne son id."""
    conn = get_conn()
    try:
        c = conn.execute(
            "INSERT INTO events (user_id, type_id, points, raison, created_by) VALUES (?,?,?,?,?)",
            (user_id, type_id, points, raison, created_by)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def get_event_by_id(event_id):
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT e.*, et.code, et.label, et.direction, et.email_subject, et.email_template,
                   u.nom, u.prenom, u.email, u.ad_login
            FROM events e
            JOIN event_types et ON et.id = e.type_id
            JOIN users u ON u.id = e.user_id
            WHERE e.id=?
        """, (event_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_recent_events(limit=5):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT e.id, e.points, e.raison, e.created_at, e.annule,
                   et.label, et.direction,
                   u.nom, u.prenom, u.ad_login, u.id as user_id
            FROM events e
            JOIN event_types et ON et.id = e.type_id
            JOIN users u ON u.id = e.user_id
            ORDER BY e.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_events_filtered(search="", direction="", statut="", type_id="",
                             date_from="", date_to="", sort="date_desc"):
    """Retourne tous les événements avec filtres dynamiques."""
    conn = get_conn()
    try:
        conditions, params = [], []

        if search:
            like = f"%{search}%"
            conditions.append("(u.nom LIKE ? OR u.prenom LIKE ? OR u.ad_login LIKE ?)")
            params += [like, like, like]
        if direction in ("+", "-"):
            conditions.append("et.direction = ?")
            params.append(direction)
        if statut == "actif":
            conditions.append("e.annule = 0")
        elif statut == "annule":
            conditions.append("e.annule = 1")
        if type_id:
            conditions.append("e.type_id = ?")
            params.append(int(type_id))
        if date_from:
            conditions.append("date(e.created_at) >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("date(e.created_at) <= ?")
            params.append(date_to)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        order = {
            "date_desc":   "e.created_at DESC",
            "date_asc":    "e.created_at ASC",
            "points_desc": "ABS(e.points) DESC",
            "points_asc":  "ABS(e.points) ASC",
            "user":        "u.nom ASC, u.prenom ASC",
        }.get(sort, "e.created_at DESC")

        rows = conn.execute(f"""
            SELECT e.id, e.points, e.raison, e.created_at, e.annule, e.created_by,
                   et.label, et.direction, et.code,
                   u.nom, u.prenom, u.ad_login, u.id as user_id
            FROM events e
            JOIN event_types et ON et.id = e.type_id
            JOIN users u ON u.id = e.user_id
            {where}
            ORDER BY {order}
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_events(user_id):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT e.id, e.points, e.raison, e.created_at, e.annule, e.created_by,
                   et.label, et.direction, et.code
            FROM events e
            JOIN event_types et ON et.id = e.type_id
            WHERE e.user_id=?
            ORDER BY e.created_at DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def annuler_event(event_id):
    """Marque l'événement comme annulé. Retourne True si OK."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM events WHERE id=? AND annule=0", (event_id,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE events SET annule=1 WHERE id=?", (event_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def update_event(event_id, type_id, points, raison):
    """Met à jour le type, les points et la raison d'un événement."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE events SET type_id=?, points=?, raison=? WHERE id=?",
            (type_id, points, raison, event_id)
        )
        conn.commit()
    finally:
        conn.close()


def user_has_coffre_mdp(user_id):
    """Vérifie si l'utilisateur a déjà reçu COFFRE_MDP (non annulé). Déprécié, utiliser user_has_one_shot_event."""
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT e.id FROM events e
            JOIN event_types et ON et.id = e.type_id
            WHERE e.user_id=? AND et.code='COFFRE_MDP' AND e.annule=0
        """, (user_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def user_has_one_shot_event(user_id, type_id):
    """Vérifie si l'utilisateur a déjà un événement non annulé de ce type."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM events WHERE user_id=? AND type_id=? AND annule=0",
            (user_id, type_id)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_user_monthly_bonus(user_id):
    """Retourne la somme des points bonus (direction='+') du mois en cours pour un utilisateur."""
    conn = get_conn()
    try:
        month_start = datetime.now().strftime("%Y-%m-01")
        row = conn.execute("""
            SELECT COALESCE(SUM(e.points), 0) as total
            FROM events e
            JOIN event_types et ON et.id = e.type_id
            WHERE e.user_id=? AND e.annule=0 AND et.direction='+'
              AND e.created_at >= ?
        """, (user_id, month_start)).fetchone()
        return row["total"]
    finally:
        conn.close()


def get_inactive_users_last_month():
    """Retourne les utilisateurs actifs sans événement bonus dans le mois en cours."""
    conn = get_conn()
    try:
        month_start = datetime.now().strftime("%Y-%m-01")
        rows = conn.execute("""
            SELECT u.id, u.ad_login, u.nom, u.prenom, u.score_total
            FROM users u
            WHERE u.actif=1 AND u.is_tester=0
              AND u.id NOT IN (
                  SELECT DISTINCT e.user_id FROM events e
                  JOIN event_types et ON et.id = e.type_id
                  WHERE e.annule=0 AND et.direction='+' AND e.created_at >= ?
              )
        """, (month_start,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Score & Niveau ───────────────────────────────────────────────────────────

def update_user_score(user_id, new_score, new_niveau):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET score_total=?, niveau=? WHERE id=?",
            (new_score, new_niveau, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def recalculate_user_score(user_id):
    """Recalcule le score depuis zéro à partir des événements non annulés."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT points FROM events WHERE user_id=? AND annule=0",
            (user_id,)
        ).fetchall()
        total = sum(r["points"] for r in rows)
        total = max(0, total)
        return total
    finally:
        conn.close()


# ─── Notifications ────────────────────────────────────────────────────────────

def insert_notification(event_id, user_id):
    conn = get_conn()
    try:
        c = conn.execute(
            "INSERT INTO notifications (event_id, user_id) VALUES (?,?)",
            (event_id, user_id)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def update_notification_status(notif_id, status, error_msg=None):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE notifications SET status=?, sent_at=datetime('now','localtime'), error_msg=? WHERE id=?",
            (status, error_msg, notif_id)
        )
        conn.commit()
    finally:
        conn.close()


# ─── Leaderboard ──────────────────────────────────────────────────────────────

def get_leaderboard():
    """Retourne les utilisateurs actifs non-testeurs triés par score décroissant."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT id, ad_login, nom, prenom, score_total, niveau
            FROM users WHERE actif=1 AND is_tester=0
            ORDER BY score_total DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def snapshot_leaderboard():
    """Enregistre un snapshot quotidien du leaderboard."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        # Supprime le snapshot du jour s'il existe déjà
        conn.execute("DELETE FROM leaderboard_log WHERE snapshot_date=?", (today,))
        users = conn.execute(
            "SELECT id, score_total, niveau FROM users WHERE actif=1 AND is_tester=0 ORDER BY score_total DESC"
        ).fetchall()
        for rang, u in enumerate(users, 1):
            conn.execute(
                "INSERT INTO leaderboard_log (user_id, rang, score, niveau, snapshot_date) VALUES (?,?,?,?,?)",
                (u["id"], rang, u["score_total"], u["niveau"], today)
            )
        conn.commit()
    finally:
        conn.close()


def get_yesterday_rank(user_id):
    """Retourne le rang de l'utilisateur hier pour calculer la progression."""
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT rang, score FROM leaderboard_log WHERE user_id=? AND snapshot_date=?",
            (user_id, yesterday)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ─── Statistiques dashboard ───────────────────────────────────────────────────

def get_stats_today():
    """Statistiques du jour pour le dashboard."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        nb_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE date(created_at)=? AND annule=0", (today,)
        ).fetchone()[0]

        avg_score = conn.execute(
            "SELECT AVG(score_total) FROM users WHERE actif=1 AND is_tester=0"
        ).fetchone()[0]

        niveaux = conn.execute(
            "SELECT niveau, COUNT(*) as cnt FROM users WHERE actif=1 AND is_tester=0 GROUP BY niveau"
        ).fetchall()
        niveaux_dict = {r["niveau"]: r["cnt"] for r in niveaux}

        nb_users = conn.execute("SELECT COUNT(*) FROM users WHERE actif=1 AND is_tester=0").fetchone()[0]

        return {
            "nb_events_today": nb_events,
            "avg_score": round(avg_score or 0, 1),
            "niveaux": niveaux_dict,
            "nb_users": nb_users,
        }
    finally:
        conn.close()


# ─── Quiz — CRUD ───────────────────────────────────────────────────────────────

import secrets as _secrets


def create_quiz(title, description, date_limite, seuil_reussite, created_by):
    """Crée un quiz vide et retourne son id."""
    conn = get_conn()
    try:
        c = conn.execute(
            "INSERT INTO quizzes (title, description, date_limite, seuil_reussite, created_by) VALUES (?,?,?,?,?)",
            (title, description or None, date_limite or None, int(seuil_reussite), created_by)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def add_quiz_question(quiz_id, question_text, ordre, multiple_answers=False):
    """Ajoute une question au quiz et retourne son id."""
    conn = get_conn()
    try:
        c = conn.execute(
            "INSERT INTO quiz_questions (quiz_id, question_text, ordre, multiple_answers) VALUES (?,?,?,?)",
            (quiz_id, question_text, ordre, 1 if multiple_answers else 0)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def add_quiz_choice(question_id, choice_text, is_correct, ordre):
    """Ajoute un choix à une question."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO quiz_choices (question_id, choice_text, is_correct, ordre) VALUES (?,?,?,?)",
            (question_id, choice_text, 1 if is_correct else 0, ordre)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_quizzes():
    """Retourne tous les quizzes avec stats de participation."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT q.*,
                   COUNT(DISTINCT qa.id)                                        AS nb_attempts,
                   COUNT(DISTINCT CASE WHEN qa.status='completed' THEN qa.id END) AS nb_completed,
                   COUNT(DISTINCT qq.id)                                        AS nb_questions
            FROM quizzes q
            LEFT JOIN quiz_attempts  qa ON qa.quiz_id = q.id
            LEFT JOIN quiz_questions qq ON qq.quiz_id = q.id
            GROUP BY q.id
            ORDER BY q.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_quiz_by_id(quiz_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM quizzes WHERE id=?", (quiz_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_quiz_questions(quiz_id):
    """Retourne les questions d'un quiz avec leurs choix imbriqués."""
    conn = get_conn()
    try:
        questions = conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_id=? ORDER BY ordre", (quiz_id,)
        ).fetchall()
        result = []
        for q in questions:
            choices = conn.execute(
                "SELECT * FROM quiz_choices WHERE question_id=? ORDER BY ordre", (q["id"],)
            ).fetchall()
            result.append({**dict(q), "choices": [dict(c) for c in choices]})
        return result
    finally:
        conn.close()


def get_quiz_attempt_by_token(token):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT qa.*, u.nom, u.prenom, u.email FROM quiz_attempts qa JOIN users u ON u.id=qa.user_id WHERE qa.token=?",
            (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_quiz_attempt(quiz_id, user_id):
    """Crée une tentative avec token unique. Retourne le token."""
    token = _secrets.token_urlsafe(32)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO quiz_attempts (quiz_id, user_id, token) VALUES (?,?,?)",
            (quiz_id, user_id, token)
        )
        conn.commit()
        return token
    finally:
        conn.close()


def mark_quiz_attempt_sent(token):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE quiz_attempts SET sent_at=datetime('now','localtime') WHERE token=?", (token,)
        )
        conn.commit()
    finally:
        conn.close()


def start_quiz_attempt(token):
    """Marque le début du quiz (started_at)."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE quiz_attempts SET started_at=datetime('now','localtime') WHERE token=? AND started_at IS NULL",
            (token,)
        )
        conn.commit()
    finally:
        conn.close()


def complete_quiz_attempt(token, score_pct, nb_correct, nb_total, points_awarded):
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE quiz_attempts
            SET status='completed', completed_at=datetime('now','localtime'),
                score_pct=?, nb_correct=?, nb_total=?, points_awarded=?
            WHERE token=?
        """, (score_pct, nb_correct, nb_total, points_awarded, token))
        conn.commit()
    finally:
        conn.close()


def get_users_without_quiz_attempt(quiz_id):
    """Retourne les utilisateurs actifs non-testeurs sans tentative réelle pour ce quiz."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT u.* FROM users u
            WHERE u.actif=1
            AND u.is_tester=0
            AND u.id NOT IN (
                SELECT user_id FROM quiz_attempts
                WHERE quiz_id=? AND (is_test=0 OR is_test IS NULL)
            )
        """, (quiz_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_quiz_results(quiz_id, is_test=False):
    """Retourne les tentatives réelles ou de test d'un quiz avec infos utilisateur."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT qa.*, u.nom, u.prenom, u.ad_login
            FROM quiz_attempts qa
            JOIN users u ON u.id = qa.user_id
            WHERE qa.quiz_id=? AND qa.is_test=?
            ORDER BY qa.score_pct DESC NULLS LAST, qa.completed_at
        """, (quiz_id, 1 if is_test else 0)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_quiz(quiz_id, title, description, date_limite, seuil_reussite):
    """Met à jour les métadonnées d'un quiz existant."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE quizzes SET title=?, description=?, date_limite=?, seuil_reussite=? WHERE id=?",
            (title, description or None, date_limite or None, int(seuil_reussite), quiz_id)
        )
        conn.commit()
    finally:
        conn.close()


def delete_quiz_questions_and_choices(quiz_id):
    """Supprime toutes les questions et choix d'un quiz (pour réinsertion lors d'une édition)."""
    conn = get_conn()
    try:
        qids = [r[0] for r in conn.execute("SELECT id FROM quiz_questions WHERE quiz_id=?", (quiz_id,)).fetchall()]
        for qid in qids:
            conn.execute("DELETE FROM quiz_choices WHERE question_id=?", (qid,))
        conn.execute("DELETE FROM quiz_questions WHERE quiz_id=?", (quiz_id,))
        conn.commit()
    finally:
        conn.close()


def create_test_quiz_attempt(quiz_id, user_id):
    """Crée une tentative de test (is_test=1), remplace une éventuelle précédente. Retourne le token."""
    token = _secrets.token_urlsafe(32)
    conn = get_conn()
    try:
        # Supprimer d'abord les réponses liées aux tentatives de test existantes (FK)
        old_ids = [r[0] for r in conn.execute(
            "SELECT id FROM quiz_attempts WHERE quiz_id=? AND user_id=? AND is_test=1",
            (quiz_id, user_id)
        ).fetchall()]
        for aid in old_ids:
            conn.execute("DELETE FROM quiz_attempt_answers WHERE attempt_id=?", (aid,))
        conn.execute(
            "DELETE FROM quiz_attempts WHERE quiz_id=? AND user_id=? AND is_test=1",
            (quiz_id, user_id)
        )
        conn.execute(
            "INSERT INTO quiz_attempts (quiz_id, user_id, token, is_test) VALUES (?,?,?,1)",
            (quiz_id, user_id, token)
        )
        conn.commit()
        return token
    finally:
        conn.close()


def toggle_quiz_actif(quiz_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE quizzes SET actif=CASE WHEN actif=1 THEN 0 ELSE 1 END WHERE id=?", (quiz_id,))
        conn.commit()
    finally:
        conn.close()


def delete_quiz(quiz_id):
    """Supprime un quiz et tout son contenu (si aucune tentative complétée)."""
    conn = get_conn()
    try:
        done = conn.execute(
            "SELECT COUNT(*) FROM quiz_attempts WHERE quiz_id=? AND status='completed'", (quiz_id,)
        ).fetchone()[0]
        if done > 0:
            return False, f"{done} participant(s) ont déjà répondu, suppression impossible."
        # Cascade manuelle (FK sans ON DELETE CASCADE)
        qids = [r[0] for r in conn.execute("SELECT id FROM quiz_questions WHERE quiz_id=?", (quiz_id,)).fetchall()]
        for qid in qids:
            conn.execute("DELETE FROM quiz_choices WHERE question_id=?", (qid,))
        conn.execute("DELETE FROM quiz_questions WHERE quiz_id=?", (quiz_id,))
        conn.execute("DELETE FROM quiz_attempts WHERE quiz_id=?", (quiz_id,))
        conn.execute("DELETE FROM quizzes WHERE id=?", (quiz_id,))
        conn.commit()
        return True, None
    finally:
        conn.close()


def user_has_quiz_attempt(quiz_id, user_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM quiz_attempts WHERE quiz_id=? AND user_id=?", (quiz_id, user_id)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_attempt_answers(attempt_id, question_choice_pairs):
    """Stocke les réponses sélectionnées par le candidat.
    question_choice_pairs = liste de (question_id, choice_id).
    """
    conn = get_conn()
    try:
        conn.execute("DELETE FROM quiz_attempt_answers WHERE attempt_id=?", (attempt_id,))
        for qid, cid in question_choice_pairs:
            conn.execute(
                "INSERT INTO quiz_attempt_answers (attempt_id, question_id, choice_id) VALUES (?,?,?)",
                (attempt_id, qid, cid)
            )
        conn.commit()
    finally:
        conn.close()


def get_attempt_answers(attempt_id):
    """Retourne les réponses sélectionnées sous forme {question_id: set(choice_ids)}."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT question_id, choice_id FROM quiz_attempt_answers WHERE attempt_id=?",
            (attempt_id,)
        ).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["question_id"], set()).add(r["choice_id"])
        return result
    finally:
        conn.close()


def get_pending_attempts_with_users(quiz_id):
    """Retourne les tentatives pending (non-test, non-complétées) avec infos utilisateur."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT qa.*, u.nom, u.prenom, u.email
            FROM quiz_attempts qa
            JOIN users u ON u.id = qa.user_id
            WHERE qa.quiz_id=? AND qa.is_test=0 AND qa.status='pending'
        """, (quiz_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Formations ──────────────────────────────────────────────────────────────

def create_formation(title, description, mois, created_by):
    """Crée une formation et retourne son id."""
    conn = get_conn()
    try:
        c = conn.execute(
            "INSERT INTO formations (title, description, mois, created_by) VALUES (?,?,?,?)",
            (title, description or None, mois, created_by)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def update_formation(formation_id, title, description, mois):
    """Met à jour les métadonnées d'une formation."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE formations SET title=?, description=?, mois=? WHERE id=?",
            (title, description or None, mois, formation_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_formations():
    """Retourne toutes les formations avec le nombre de ressources."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT f.*, COUNT(fr.id) AS nb_resources
            FROM formations f
            LEFT JOIN formation_resources fr ON fr.formation_id = f.id
            GROUP BY f.id
            ORDER BY f.mois DESC, f.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_formations():
    """Retourne les formations actives (page publique)."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT f.*, COUNT(fr.id) AS nb_resources
            FROM formations f
            LEFT JOIN formation_resources fr ON fr.formation_id = f.id
            WHERE f.actif=1
            GROUP BY f.id
            ORDER BY f.mois DESC, f.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_formation_by_id(formation_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM formations WHERE id=?", (formation_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def toggle_formation_actif(formation_id):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE formations SET actif=CASE WHEN actif=1 THEN 0 ELSE 1 END WHERE id=?",
            (formation_id,)
        )
        conn.commit()
    finally:
        conn.close()


def delete_formation(formation_id):
    """Supprime une formation et toutes ses ressources."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM formation_resources WHERE formation_id=?", (formation_id,))
        conn.execute("DELETE FROM formations WHERE id=?", (formation_id,))
        conn.commit()
        return True, None
    finally:
        conn.close()


# ─── Ressources de formation ────────────────────────────────────────────────

def add_formation_resource(formation_id, title, resource_type, url=None,
                           file_path=None, file_name=None, description=None, ordre=0):
    """Ajoute une ressource à une formation. Retourne son id."""
    conn = get_conn()
    try:
        c = conn.execute(
            """INSERT INTO formation_resources
               (formation_id, title, resource_type, url, file_path, file_name, description, ordre)
               VALUES (?,?,?,?,?,?,?,?)""",
            (formation_id, title, resource_type, url, file_path, file_name, description, ordre)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def get_formation_resources(formation_id):
    """Retourne les ressources d'une formation triées par ordre."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM formation_resources WHERE formation_id=? ORDER BY ordre, id",
            (formation_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_formation_resource_by_id(resource_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM formation_resources WHERE id=?", (resource_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_formation_resource(resource_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM formation_resources WHERE id=?", (resource_id,))
        conn.commit()
    finally:
        conn.close()
