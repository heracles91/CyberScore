# app.py — Application Flask CyberScore
# Lancement : python app.py  →  http://localhost:5000

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, g, Response)
from flask_wtf.csrf import CSRFProtect
from datetime import timedelta, datetime
import functools
import re
import io
import csv
import unicodedata

import os

import config
import database as db
import models
import socket
import subprocess
import re as _re
import mailer

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
ALLOWED_EXTENSIONS = {
    "pdf", "pptx", "ppt", "docx", "doc", "xlsx", "xls",
    "png", "jpg", "jpeg", "gif", "mp4", "mp3", "zip", "txt",
}

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=config.SESSION_TIMEOUT_HOURS)
csrf = CSRFProtect(app)


@app.context_processor
def inject_globals():
    """Injecte les variables globales dans tous les templates."""
    try:
        nb_pending = db.count_pending_session_reports()
    except Exception:
        nb_pending = 0
    return {
        "config": config,
        # Valeurs modifiables via l'interface (priorité BDD > config.py)
        "company_name": db.get_setting("company_name", config.COMPANY_NAME),
        "support_email": db.get_setting("support_email", config.SUPPORT_EMAIL),
        "nb_pending_reports": nb_pending,
    }


def _get_network_info(ip):
    """Résout le nom d'hôte et la MAC depuis l'IP du requérant."""
    info = {"ip": ip, "hostname": None, "mac": None}
    if not ip or ip in ("127.0.0.1", "::1"):
        return info
    try:
        info["hostname"] = socket.gethostbyaddr(ip)[0].split(".")[0].upper()
    except Exception:
        pass
    try:
        subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                       capture_output=True, timeout=2)
        out = subprocess.run(["ip", "neigh", "show", ip],
                             capture_output=True, text=True, timeout=2).stdout
        m = _re.search(r"lladdr\s+([0-9a-f:]{17})", out, _re.IGNORECASE)
        if m:
            info["mac"] = m.group(1).upper()
    except Exception:
        pass
    return info


def _check_inventory_match(ad_login, requester_ip, requester_hostname):
    """Retourne (match: 'yes'|'no'|'unknown', note: str)."""
    entries = db.get_inventory_by_ad_login(ad_login)
    if not entries:
        return "unknown", f"Aucun poste trouvé dans l'inventaire pour {ad_login}."
    for e in entries:
        hn_ok = bool(requester_hostname and e["id_cap2i"] and
                     e["id_cap2i"].upper() == requester_hostname.upper())
        ip_ok = bool(requester_ip and e["ip"] and e["ip"] == requester_ip)
        if hn_ok or ip_ok:
            details = []
            if hn_ok: details.append('hostname "' + requester_hostname + '" = ' + e['id_cap2i'])
            if ip_ok: details.append(f"IP {requester_ip} = {e['ip']}")
            return "yes", (
                f"✅ Correspondance inventaire ({', '.join(details)}) — "
                f"c'est bien le poste attribué à l'utilisateur signalé."
            )
    pc_list = ", ".join(
        f"{e['id_cap2i']} ({e['ip'] or 'IP inconnue'})" for e in entries
    )
    return "no", (
        f"⚠️ Pas de correspondance : le requérant "
        f"({requester_hostname or requester_ip or 'inconnu'}) "
        f"n'est pas le poste de {ad_login} dans l'inventaire ({pc_list})."
    )


def _get_company_name():
    return db.get_setting("company_name", config.COMPANY_NAME)


def _get_support_email():
    return db.get_setting("support_email", config.SUPPORT_EMAIL)


def _get_admin_pass():
    return db.get_setting("admin_pass", config.ADMIN_PASS)


# ─── Helpers CSV ──────────────────────────────────────────────────────────────

def _normalize_str(s):
    """Supprime les accents et met en minuscules."""
    return ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if unicodedata.category(c) != 'Mn'
    ).lower().replace(' ', '').replace('-', '').replace("'", '')


def _generate_ad_login(prenom, nom):
    """Génère un login AD unique : 1ère lettre prénom + nom, sans accent."""
    base = _normalize_str(prenom[:1]) + _normalize_str(nom)
    base = re.sub(r'[^a-z0-9]', '', base)
    login = base
    counter = 2
    while db.login_exists_in_db(login):
        login = base + str(counter)
        counter += 1
    return login


# ─── Initialisation BDD ───────────────────────────────────────────────────────

@app.before_request
def before_request():
    """Initialise la BDD si besoin (sécurisé par le flag d'init)."""
    if not getattr(app, "_db_initialized", False):
        db.init_db()
        app._db_initialized = True


# ─── Décorateur authentification ─────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ─── Login / Logout ───────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pwd  = request.form.get("password", "")
        if user == config.ADMIN_USER and pwd == _get_admin_pass():
            session.permanent = True
            session["admin_logged_in"] = True
            session["admin_user"] = user
            next_url = request.args.get("next", "")
            # Protection open redirect : n'accepter que les chemins relatifs internes
            if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        error = "Identifiants incorrects."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    leaderboard = models.get_leaderboard_with_progression()
    events = db.get_recent_events(10)
    stats = db.get_stats_today()
    nb_pending_unlock = db.count_pending_session_reports()
    return render_template("dashboard.html",
                           leaderboard=leaderboard,
                           events=events,
                           stats=stats,
                           nb_pending_unlock=nb_pending_unlock)


# ─── Utilisateurs ─────────────────────────────────────────────────────────────

@app.route("/users")
@login_required
def users():
    all_users = db.get_all_users()
    return render_template("users.html", users=all_users)


@app.route("/users/add", methods=["GET", "POST"])
@login_required
def user_add():
    if request.method == "POST":
        ad_login = request.form.get("ad_login", "").strip()
        nom      = request.form.get("nom", "").strip()
        prenom   = request.form.get("prenom", "").strip()
        email    = request.form.get("email", "").strip()

        # Validations
        errors = []
        if not ad_login:
            errors.append("Le login AD est obligatoire.")
        if not nom:
            errors.append("Le nom est obligatoire.")
        if not prenom:
            errors.append("Le prénom est obligatoire.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errors.append("L'adresse email n'est pas valide.")

        if not errors:
            ok, err = db.create_user(ad_login, nom, prenom, email)
            if ok:
                flash(f"Utilisateur {prenom} {nom} créé avec succès.", "success")
                return redirect(url_for("users"))
            else:
                if "UNIQUE" in str(err):
                    errors.append(f"Le login AD '{ad_login}' existe déjà.")
                else:
                    errors.append(f"Erreur : {err}")

        for e in errors:
            flash(e, "error")
        return render_template("user_add.html",
                               form=request.form)

    return render_template("user_add.html", form={})


@app.route("/users/<int:user_id>")
@login_required
def user_detail(user_id):
    user = db.get_user_by_id(user_id)
    if not user:
        flash("Utilisateur introuvable.", "error")
        return redirect(url_for("users"))
    events = db.get_user_events(user_id)
    return render_template("user_detail.html", user=user, events=events)


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
def user_toggle(user_id):
    db.toggle_user_actif(user_id)
    return redirect(url_for("user_detail", user_id=user_id))


@app.route("/users/<int:user_id>/toggle-tester", methods=["POST"])
@login_required
def user_toggle_tester(user_id):
    db.toggle_user_tester(user_id)
    return redirect(url_for("user_detail", user_id=user_id))


# ─── Import CSV utilisateurs ───────────────────────────────────────────────────

@app.route("/users/import/sample")
@login_required
def users_import_sample():
    """Télécharge un fichier CSV exemple."""
    content = "First Name,Last Name,Email,Position\nJean,DUPONT,j.dupont@maboite.fr,Comptable\nMarie,MARTIN,m.martin@maboite.fr,RH\n"
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sample_utilisateurs.csv"}
    )


@app.route("/users/import", methods=["GET", "POST"])
@login_required
def users_import():
    results = None
    if request.method == "POST":
        f = request.files.get("csvfile")
        if not f or not f.filename.endswith(".csv"):
            flash("Veuillez sélectionner un fichier .csv valide.", "error")
            return redirect(url_for("users_import"))

        stream = io.StringIO(f.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)

        # Vérification colonnes
        required = {"First Name", "Last Name", "Email"}
        if not required.issubset(set(reader.fieldnames or [])):
            flash(f"Colonnes attendues : First Name, Last Name, Email, Position. Trouvées : {', '.join(reader.fieldnames or [])}", "error")
            return redirect(url_for("users_import"))

        results = []
        for i, row in enumerate(reader, start=2):
            prenom   = row.get("First Name", "").strip()
            nom      = row.get("Last Name", "").strip().upper()
            email    = row.get("Email", "").strip().lower()

            # Validation
            if not prenom or not nom:
                results.append({"ligne": i, "status": "erreur", "msg": "Prénom ou nom vide", "prenom": prenom, "nom": nom, "email": email, "login": ""})
                continue
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                results.append({"ligne": i, "status": "erreur", "msg": f"Email invalide : {email}", "prenom": prenom, "nom": nom, "email": email, "login": ""})
                continue

            ad_login = _generate_ad_login(prenom, nom)
            ok, err = db.create_user(ad_login, nom, prenom, email)
            if ok:
                results.append({"ligne": i, "status": "ok", "msg": "Créé", "prenom": prenom, "nom": nom, "email": email, "login": ad_login})
            else:
                results.append({"ligne": i, "status": "erreur", "msg": err, "prenom": prenom, "nom": nom, "email": email, "login": ad_login})

    return render_template("users_import.html", results=results)


# ─── Événements ───────────────────────────────────────────────────────────────

@app.route("/events")
@login_required
def events_list():
    search     = request.args.get("search", "").strip()
    direction  = request.args.get("direction", "")
    statut     = request.args.get("statut", "")
    type_id    = request.args.get("type_id", "")
    date_from  = request.args.get("date_from", "")
    date_to    = request.args.get("date_to", "")
    sort       = request.args.get("sort", "date_desc")

    events      = db.get_all_events_filtered(search, direction, statut, type_id, date_from, date_to, sort)
    event_types = db.get_all_event_types()
    return render_template("events_list.html",
                           events=events, event_types=event_types,
                           search=search, direction=direction, statut=statut,
                           type_id=type_id, date_from=date_from, date_to=date_to,
                           sort=sort)

@app.route("/events/add", methods=["GET", "POST"])
@login_required
def event_add():
    event_types = db.get_all_event_types()
    # Préselection utilisateur via query string (depuis /users/<id>)
    preselect_user_id = request.args.get("user_id", "")
    preselect_user = None
    if preselect_user_id:
        preselect_user = db.get_user_by_id(int(preselect_user_id))

    return render_template("event_add.html",
                           event_types=event_types,
                           preselect_user=preselect_user)


@app.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
def event_edit(event_id):
    event = db.get_event_by_id(event_id)
    if not event:
        flash("Événement introuvable.", "error")
        return redirect(url_for("events_list"))
    if event["annule"]:
        flash("Impossible de modifier un événement annulé.", "error")
        return redirect(url_for("events_list"))

    event_types = db.get_all_event_types()

    if request.method == "POST":
        new_type_id = request.form.get("type_id", "")
        new_points  = request.form.get("points", "")
        new_raison  = request.form.get("raison", "").strip()

        errors = []
        if not new_type_id:
            errors.append("Le type d'événement est obligatoire.")
        try:
            pts = int(new_points)
        except (ValueError, TypeError):
            errors.append("Les points doivent être un entier.")
            pts = 0

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("event_edit.html", event=event, event_types=event_types)

        ok, msg = models.edit_event(event_id, int(new_type_id), pts, new_raison)
        if ok:
            flash(msg, "success")
            return redirect(url_for("events_list"))
        else:
            flash(msg, "error")
            return render_template("event_edit.html", event=event, event_types=event_types)

    return render_template("event_edit.html", event=event, event_types=event_types)


@app.route("/events/cancel/<int:event_id>", methods=["POST"])
@login_required
def event_cancel(event_id):
    admin = session.get("admin_user", "admin")
    ok, msg = models.cancel_event(event_id, admin)
    if ok:
        flash(msg, "success")
    else:
        flash(msg, "error")
    # Retour vers la page précédente
    ref = request.referrer or url_for("dashboard")
    return redirect(ref)


# ─── API interne ──────────────────────────────────────────────────────────────

@app.route("/api/users/search")
@login_required
def api_users_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(db.search_users(q))


@app.route("/api/events/add", methods=["POST"])
@csrf.exempt
def api_events_add():
    """
    Endpoint POST JSON pour ajouter un événement.
    Accepte les requêtes authentifiées par session OU par clé API (header X-API-Key).
    Usage webhook GoPhish : POST /api/events/add
    Body JSON : { "ad_login": "...", "event_code": "...", "raison": "...", "points_override": null }
    """
    # Authentification : session admin OU clé API (optionnel, à configurer)
    if not session.get("admin_logged_in"):
        api_key = request.headers.get("X-API-Key", "")
        if not api_key or api_key != getattr(config, "API_KEY", None):
            return jsonify({"ok": False, "error": "Non autorisé"}), 403

    data = request.get_json(force=True, silent=True) or {}
    ad_login      = data.get("ad_login", "").strip()
    event_code    = data.get("event_code", "").strip()
    raison        = data.get("raison", "")
    points_override = data.get("points_override")  # None = utilise le défaut

    if not ad_login or not event_code:
        return jsonify({"ok": False, "error": "ad_login et event_code sont obligatoires"}), 400

    user = db.get_user_by_login(ad_login)
    if not user:
        return jsonify({"ok": False, "error": f"Utilisateur '{ad_login}' introuvable"}), 404

    etype = db.get_event_type_by_code(event_code)
    if not etype:
        return jsonify({"ok": False, "error": f"Code événement '{event_code}' inconnu"}), 404

    pts = int(points_override) if points_override is not None else None
    admin = session.get("admin_user", "api")
    ok, msg, event_id = models.add_event(user["id"], etype["id"], pts, raison, admin)

    if ok:
        updated = db.get_user_by_id(user["id"])
        return jsonify({
            "ok": True,
            "event_id": event_id,
            "nouveau_score": updated["score_total"],
            "niveau": updated["niveau"],
        })
    else:
        return jsonify({"ok": False, "error": msg}), 400


@app.route("/api/leaderboard")
def api_leaderboard():
    """Retourne le leaderboard en JSON (page publique)."""
    lb = db.get_leaderboard()
    result = []
    for rang, u in enumerate(lb, 1):
        entry = {
            "rang": rang,
            "score": u["score_total"],
            "niveau": u["niveau"],
        }
        if rang <= 10:
            entry["nom"] = u["nom"]
            entry["prenom"] = u["prenom"]
        else:
            entry["nom"] = f"Collaborateur #{rang}"
            entry["prenom"] = ""
        result.append(entry)
    return jsonify(result)


# ─── Leaderboard public ───────────────────────────────────────────────────────

@app.route("/leaderboard")
def leaderboard():
    """Page publique — pas d'authentification requise."""
    return render_template("leaderboard.html", company_name=_get_company_name())


# ─── Paramètres ───────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "app_settings":
            company = request.form.get("company_name", "").strip()
            support = request.form.get("support_email", "").strip()
            if not company:
                flash("Le nom de l'entreprise est obligatoire.", "error")
            elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", support):
                flash("Adresse email de support invalide.", "error")
            else:
                db.set_setting("company_name", company)
                db.set_setting("support_email", support)
                flash("Paramètres de l'application mis à jour.", "success")

        elif action == "change_password":
            current  = request.form.get("current_pass", "")
            new_pass = request.form.get("new_pass", "")
            confirm  = request.form.get("confirm_pass", "")
            if current != _get_admin_pass():
                flash("Mot de passe actuel incorrect.", "error")
            elif len(new_pass) < 8:
                flash("Le nouveau mot de passe doit faire au moins 8 caractères.", "error")
            elif new_pass != confirm:
                flash("Les mots de passe ne correspondent pas.", "error")
            else:
                db.set_setting("admin_pass", new_pass)
                flash("Mot de passe admin mis à jour.", "success")

        elif action == "test_email":
            to = request.form.get("test_to", "").strip()
            if not to:
                flash("Saisissez une adresse email de test.", "error")
            else:
                try:
                    mailer.send_test_email(to)
                    flash(f"Email de test envoyé à {to}.", "success")
                except Exception as e:
                    flash(f"Échec d'envoi : {e}", "error")

        elif action == "snapshot":
            db.snapshot_leaderboard()
            flash("Snapshot du leaderboard enregistré.", "success")

        elif action == "scoring_settings":
            cap_enabled = "1" if request.form.get("monthly_cap_enabled") else "0"
            cap_pts = request.form.get("monthly_cap_points", "80")
            decay_en = "1" if request.form.get("decay_enabled") else "0"
            decay_pts = request.form.get("decay_points", "-10")
            db.set_setting("monthly_cap_enabled", cap_enabled)
            db.set_setting("monthly_cap_points", cap_pts)
            db.set_setting("decay_enabled", decay_en)
            db.set_setting("decay_points", decay_pts)
            flash("Paramètres de scoring mis à jour.", "success")

        elif action == "apply_decay":
            count = models.apply_inactivity_decay()
            if count > 0:
                flash(f"Malus inactivité appliqué à {count} utilisateur(s).", "success")
            else:
                flash("Aucun utilisateur inactif ce mois, ou le decay est désactivé.", "info")

        elif action == "recalculate_all":
            count = models.recalculate_all_users()
            flash(f"Scores et niveaux recalculés pour {count} utilisateur(s).", "success")

    event_types = db.get_all_event_types()
    return render_template("settings.html",
                           cfg=config,
                           event_types=event_types,
                           current_company=_get_company_name(),
                           current_support=_get_support_email(),
                           monthly_cap_enabled=db.get_setting("monthly_cap_enabled", "0"),
                           monthly_cap_points=db.get_setting("monthly_cap_points", "80"),
                           decay_enabled=db.get_setting("decay_enabled", "0"),
                           decay_points=db.get_setting("decay_points", "-10"))


# ─── Types d'événements ────────────────────────────────────────────────────────

@app.route("/event-types/add", methods=["POST"])
@login_required
def event_type_add():
    code      = request.form.get("code", "").strip().upper()
    label     = request.form.get("label", "").strip()
    points    = request.form.get("points", "0").strip()
    direction = request.form.get("direction", "+")

    errors = []
    if not code or not re.match(r'^[A-Z0-9_]+$', code):
        errors.append("Le code doit être en MAJUSCULES (lettres, chiffres, _).")
    if not label:
        errors.append("Le libellé est obligatoire.")
    try:
        pts = int(points)
        if pts == 0:
            errors.append("Les points ne peuvent pas être 0.")
        # Cohérence direction/signe
        if direction == "+" and pts < 0:
            pts = abs(pts)
        elif direction == "-" and pts > 0:
            pts = -pts
    except ValueError:
        errors.append("Les points doivent être un entier.")

    one_shot = 1 if request.form.get("one_shot") else 0
    monthly_limit = max(0, int(request.form.get("monthly_limit", 0) or 0))

    if errors:
        for e in errors:
            flash(e, "error")
    else:
        ok, err = db.create_event_type(code, label, pts, direction, one_shot, monthly_limit)
        if ok:
            flash(f"Type d'événement « {label} » créé.", "success")
        else:
            flash(f"Erreur : {err}", "error")

    return redirect(url_for("settings") + "#event-types")


@app.route("/event-types/<int:type_id>/edit", methods=["GET", "POST"])
@login_required
def event_type_edit(type_id):
    etype = db.get_event_type_by_id(type_id)
    if not etype:
        flash("Type d'événement introuvable.", "error")
        return redirect(url_for("settings") + "#event-types")

    if request.method == "POST":
        code      = request.form.get("code", "").strip().upper()
        label     = request.form.get("label", "").strip()
        points    = request.form.get("points", "0").strip()
        direction = request.form.get("direction", "+")
        one_shot  = 1 if request.form.get("one_shot") else 0
        monthly_limit = max(0, int(request.form.get("monthly_limit", 0) or 0))

        errors = []
        if not code or not re.match(r'^[A-Z0-9_]+$', code):
            errors.append("Le code doit être en MAJUSCULES (lettres, chiffres, _).")
        if not label:
            errors.append("Le libellé est obligatoire.")
        try:
            pts = int(points)
            if pts == 0:
                errors.append("Les points ne peuvent pas être 0.")
            if direction == "+" and pts < 0:
                pts = abs(pts)
            elif direction == "-" and pts > 0:
                pts = -pts
        except ValueError:
            errors.append("Les points doivent être un entier.")
            pts = 0

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("event_type_edit.html", etype=etype)

        ok, err = db.update_event_type(type_id, code, label, pts, direction, one_shot, monthly_limit)
        if ok:
            flash(f"Type « {label} » mis à jour.", "success")
            return redirect(url_for("settings") + "#event-types")
        else:
            flash(f"Erreur : {err}", "error")
            return render_template("event_type_edit.html", etype=etype)

    return render_template("event_type_edit.html", etype=etype)


@app.route("/event-types/delete/<int:type_id>", methods=["POST"])
@login_required
def event_type_delete(type_id):
    ok, err = db.delete_event_type(type_id)
    if ok:
        flash("Type d'événement supprimé.", "success")
    else:
        flash(f"Impossible de supprimer : {err}", "error")
    return redirect(url_for("settings") + "#event-types")



# ─── Signalement poste déverrouillé (public) ─────────────────────────────────

@app.route("/report/unlock", methods=["GET", "POST"])
def report_unlock():
    users = [u for u in db.get_all_users(actif_only=True) if not u.get("is_tester")]
    etype = db.get_event_type_by_code("SESSION_OUVERTE")
    etype_points = abs(etype["points"]) if etype else 20
    company_name = _get_company_name()

    if request.method == "POST":
        user_id_raw = request.form.get("user_id", "").strip()
        reporter    = request.form.get("reporter", "").strip()

        if not reporter:
            flash("Votre nom est requis pour soumettre un signalement.", "error")
            return render_template("report_unlock.html", users=users,
                                   company_name=company_name, etype_points=etype_points)

        if not user_id_raw or not user_id_raw.isdigit():
            flash("Veuillez sélectionner un utilisateur.", "error")
            return render_template("report_unlock.html", users=users,
                                   company_name=company_name, etype_points=etype_points)

        user_id = int(user_id_raw)
        user = db.get_user_by_id(user_id)
        if not user or not user.get("actif"):
            flash("Utilisateur introuvable.", "error")
            return render_template("report_unlock.html", users=users,
                                   company_name=company_name, etype_points=etype_points)

        if not etype:
            flash("Type d’événement SESSION_OUVERTE introuvable.", "error")
            return render_template("report_unlock.html", users=users,
                                   company_name=company_name, etype_points=etype_points)

        # Anti-doublon : signalement pending déjà existant pour cet utilisateur
        pending = [r for r in db.get_pending_session_reports()
                   if r["user_id"] == user_id]
        if pending:
            flash(
                f"{user['prenom']} {user['nom']} a déjà un signalement en attente de validation. "
                "Merci, l'administrateur a été notifié.",
                "info"
            )
            return render_template("report_unlock.html", users=users,
                                   company_name=company_name, etype_points=etype_points)

        # Collecte infos réseau
        requester_ip = request.remote_addr
        net = _get_network_info(requester_ip)
        inv_match, inv_note = _check_inventory_match(
            user["ad_login"], net["ip"], net["hostname"]
        )

        db.create_session_report(
            user_id=user_id,
            reporter_name=reporter,
            reporter_ip=net["ip"],
            reporter_hostname=net["hostname"],
            reporter_mac=net["mac"],
            inventory_match=inv_match,
            inventory_note=inv_note,
        )

        flash(
            f"Signalement transmis à l'administrateur IT pour validation. Merci {reporter} !",
            "success"
        )
        return render_template("report_unlock.html", users=users,
                               company_name=company_name, etype_points=etype_points)

    return render_template("report_unlock.html", users=users,
                           company_name=company_name, etype_points=etype_points)


        user_id = int(user_id)
        user = db.get_user_by_id(user_id)
        if not user or not user.get("actif"):
            flash("Utilisateur introuvable.", "error")
            return render_template("report_unlock.html", users=users, company_name=company_name, etype_points=etype_points)

        if not etype:
            flash("Type d'événement SESSION_OUVERTE introuvable.", "error")
            return render_template("report_unlock.html", users=users, company_name=company_name, etype_points=etype_points)

        if db.get_recent_session_event(user_id, etype["id"], minutes=30):
            flash(
                f"{user['prenom']} {user['nom']} a déjà été signalé(e) récemment. "
                "Merci, le signalement a déjà été pris en compte.",
                "info"
            )
            return render_template("report_unlock.html", users=users, company_name=company_name, etype_points=etype_points)

        ok, msg, _ = models.add_event(
            user_id, etype["id"], None,
            f"Poste déverrouillé signalé par {reporter}",
            "portail_it"
        )
        if ok:
            flash(
                f"Signalement enregistré pour {user['prenom']} {user['nom']}. Merci !",
                "success"
            )
        else:
            flash(f"Impossible d'enregistrer : {msg}", "error")

        return render_template("report_unlock.html", users=users, company_name=company_name, etype_points=etype_points)

    return render_template("report_unlock.html", users=users, company_name=company_name, etype_points=etype_points)

# ─── Admin — Validation des signalements de session ───────────────────────────

@app.route("/admin/session-reports")
@login_required
def session_reports():
    reports = db.get_all_session_reports()
    return render_template("session_reports.html", reports=reports)


@app.route("/admin/session-reports/<int:report_id>/approve", methods=["POST"])
@login_required
def session_report_approve(report_id):
    report = db.get_session_report_by_id(report_id)
    if not report or report["status"] != "pending":
        flash("Signalement introuvable ou déjà traité.", "error")
        return redirect(url_for("session_reports"))

    admin = session.get("admin_user", "admin")
    admin_note = request.form.get("admin_note", "").strip()
    etype = db.get_event_type_by_code("SESSION_OUVERTE")
    if not etype:
        flash("Type SESSION_OUVERTE introuvable.", "error")
        return redirect(url_for("session_reports"))

    ok, msg, _ = models.add_event(
        report["user_id"], etype["id"], None,
        f"Poste déverrouillé — signalé par {report['reporter_name'] or 'Anonyme'}"
        + (f" — note : {admin_note}" if admin_note else ""),
        admin
    )
    if ok:
        db.resolve_session_report(report_id, "approved", admin, admin_note)
        flash(
            f"Approuvé — malus {abs(etype['points'])} pts appliqué à "
            f"{report['prenom']} {report['nom']}.",
            "success"
        )
    else:
        flash(f"Impossible d'appliquer le malus : {msg}", "error")
    return redirect(url_for("session_reports"))


@app.route("/admin/session-reports/<int:report_id>/reject", methods=["POST"])
@login_required
def session_report_reject(report_id):
    report = db.get_session_report_by_id(report_id)
    if not report or report["status"] != "pending":
        flash("Signalement introuvable ou déjà traité.", "error")
        return redirect(url_for("session_reports"))

    admin = session.get("admin_user", "admin")
    admin_note = request.form.get("admin_note", "").strip()
    db.resolve_session_report(report_id, "rejected", admin, admin_note)
    flash(
        f"Rejeté — aucun malus appliqué à {report['prenom']} {report['nom']}.",
        "info"
    )
    return redirect(url_for("session_reports"))


# ─── Quiz — Admin ─────────────────────────────────────────────────────────────

@app.route("/quizzes")
@login_required
def quiz_list():
    quizzes = db.get_all_quizzes()
    return render_template("quiz_list.html", quizzes=quizzes)


@app.route("/quizzes/add", methods=["GET", "POST"])
@login_required
def quiz_add():
    if request.method == "POST":
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        date_limite = request.form.get("date_limite", "").strip()
        seuil       = request.form.get("seuil_reussite", "80").strip()
        admin       = session.get("admin_user", "admin")

        errors = []
        if not title:
            errors.append("Le titre du quiz est obligatoire.")
        try:
            seuil_int = int(seuil)
            if not 1 <= seuil_int <= 100:
                errors.append("Le seuil doit être entre 1 et 100.")
        except ValueError:
            errors.append("Seuil invalide.")
            seuil_int = 80

        # Parsing des questions
        _QUILL_EMPTY = {"<p><br></p>", "<p></p>", ""}
        nb_q = int(request.form.get("nb_questions", 0))
        questions_data = []
        for i in range(nb_q):
            q_text = request.form.get(f"q_{i}_text", "").strip()
            if not q_text or q_text in _QUILL_EMPTY:
                continue
            multiple_answers = request.form.get(f"q_{i}_multiple", "") == "1"
            correct_indices = set(request.form.getlist(f"q_{i}_correct"))
            choices = []
            j = 0
            while True:
                ct = request.form.get(f"q_{i}_c_{j}_text", "").strip()
                if not ct:
                    if j >= 2:
                        break
                else:
                    choices.append({"text": ct, "is_correct": str(j) in correct_indices})
                j += 1
                if j > 10:
                    break
            if len(choices) < 2:
                errors.append(f"Question {i+1} : au moins 2 choix requis.")
            elif not any(c["is_correct"] for c in choices):
                errors.append(f"Question {i+1} : marquez au moins une bonne réponse.")
            else:
                questions_data.append({"text": q_text, "choices": choices, "multiple_answers": multiple_answers})

        if not questions_data:
            errors.append("Le quiz doit contenir au moins une question.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("quiz_add.html", form=request.form)

        quiz_id = db.create_quiz(title, description, date_limite or None, seuil_int, admin)
        for ordre_q, q in enumerate(questions_data):
            qid = db.add_quiz_question(quiz_id, q["text"], ordre_q, q.get("multiple_answers", False))
            for ordre_c, c in enumerate(q["choices"]):
                db.add_quiz_choice(qid, c["text"], c["is_correct"], ordre_c)

        flash(f"Quiz « {title} » créé avec {len(questions_data)} question(s).", "success")
        return redirect(url_for("quiz_detail", quiz_id=quiz_id))

    return render_template("quiz_add.html", form={})


@app.route("/quizzes/<int:quiz_id>")
@login_required
def quiz_detail(quiz_id):
    quiz = db.get_quiz_by_id(quiz_id)
    if not quiz:
        flash("Quiz introuvable.", "error")
        return redirect(url_for("quiz_list"))
    questions      = db.get_quiz_questions(quiz_id)
    results        = db.get_quiz_results(quiz_id, is_test=False)
    test_results   = db.get_quiz_results(quiz_id, is_test=True)
    nb_pending     = sum(1 for r in results if r["status"] == "pending")
    question_stats = db.get_quiz_question_stats(quiz_id)
    return render_template("quiz_detail.html",
                           quiz=quiz, questions=questions,
                           results=results, test_results=test_results,
                           nb_pending=nb_pending,
                           question_stats=question_stats)


@app.route("/quizzes/<int:quiz_id>/send", methods=["POST"])
@login_required
def quiz_send(quiz_id):
    admin = session.get("admin_user", "admin")
    sent, errors = models.send_quiz_invitations(quiz_id, admin)
    if sent == 0 and errors == 0:
        flash("Tous les utilisateurs actifs ont déjà reçu une invitation.", "info")
    elif errors:
        flash(f"{sent} invitation(s) envoyée(s). {errors} échec(s).", "error")
    else:
        flash(f"{sent} invitation(s) envoyée(s) avec succès.", "success")
    return redirect(url_for("quiz_detail", quiz_id=quiz_id))


@app.route("/quizzes/<int:quiz_id>/edit", methods=["GET", "POST"])
@login_required
def quiz_edit(quiz_id):
    quiz = db.get_quiz_by_id(quiz_id)
    if not quiz:
        flash("Quiz introuvable.", "error")
        return redirect(url_for("quiz_list"))

    if request.method == "POST":
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        date_limite = request.form.get("date_limite", "").strip()
        seuil       = request.form.get("seuil_reussite", "80").strip()
        admin       = session.get("admin_user", "admin")

        errors = []
        if not title:
            errors.append("Le titre du quiz est obligatoire.")
        try:
            seuil_int = int(seuil)
            if not 1 <= seuil_int <= 100:
                errors.append("Le seuil doit être entre 1 et 100.")
        except ValueError:
            errors.append("Seuil invalide.")
            seuil_int = 80

        _QUILL_EMPTY = {"<p><br></p>", "<p></p>", ""}
        nb_q = int(request.form.get("nb_questions", 0))
        questions_data = []
        for i in range(nb_q):
            q_text = request.form.get(f"q_{i}_text", "").strip()
            if not q_text or q_text in _QUILL_EMPTY:
                continue
            multiple_answers = request.form.get(f"q_{i}_multiple", "") == "1"
            correct_indices  = set(request.form.getlist(f"q_{i}_correct"))
            choices = []
            j = 0
            while True:
                ct = request.form.get(f"q_{i}_c_{j}_text", "").strip()
                if not ct:
                    if j >= 2:
                        break
                else:
                    choices.append({"text": ct, "is_correct": str(j) in correct_indices})
                j += 1
                if j > 10:
                    break
            if len(choices) < 2:
                errors.append(f"Question {i+1} : au moins 2 choix requis.")
            elif not any(c["is_correct"] for c in choices):
                errors.append(f"Question {i+1} : marquez au moins une bonne réponse.")
            else:
                questions_data.append({"text": q_text, "choices": choices, "multiple_answers": multiple_answers})

        if not questions_data:
            errors.append("Le quiz doit contenir au moins une question.")

        if errors:
            for e in errors:
                flash(e, "error")
            existing_questions = db.get_quiz_questions(quiz_id)
            return render_template("quiz_edit.html", quiz=quiz, questions=existing_questions)

        db.update_quiz(quiz_id, title, description, date_limite or None, seuil_int)
        db.delete_quiz_questions_and_choices(quiz_id)
        for ordre_q, q in enumerate(questions_data):
            qid = db.add_quiz_question(quiz_id, q["text"], ordre_q, q.get("multiple_answers", False))
            for ordre_c, c in enumerate(q["choices"]):
                db.add_quiz_choice(qid, c["text"], c["is_correct"], ordre_c)

        flash(f"Quiz « {title} » mis à jour avec {len(questions_data)} question(s).", "success")
        return redirect(url_for("quiz_detail", quiz_id=quiz_id))

    questions = db.get_quiz_questions(quiz_id)
    return render_template("quiz_edit.html", quiz=quiz, questions=questions)


@app.route("/quizzes/<int:quiz_id>/preview", methods=["POST"])
@login_required
def quiz_preview(quiz_id):
    quiz = db.get_quiz_by_id(quiz_id)
    if not quiz:
        flash("Quiz introuvable.", "error")
        return redirect(url_for("quiz_list"))

    if not db.get_quiz_questions(quiz_id):
        flash("Ce quiz n'a pas encore de questions.", "error")
        return redirect(url_for("quiz_detail", quiz_id=quiz_id))

    testers = db.get_tester_users()
    if not testers:
        flash("Aucun testeur trouvé. Marquez un utilisateur comme testeur dans la fiche utilisateur.", "error")
        return redirect(url_for("quiz_detail", quiz_id=quiz_id))

    tester = testers[0]
    token = db.create_test_quiz_attempt(quiz_id, tester["id"])
    flash(f"Mode prévisualisation — compte : {tester['prenom']} {tester['nom']} (aucun point attribué)", "info")
    return redirect(url_for("quiz_take", token=token))


@app.route("/quizzes/<int:quiz_id>/remind", methods=["POST"])
@login_required
def quiz_remind(quiz_id):
    admin = session.get("admin_user", "admin")
    sent, errors = models.send_quiz_reminders(quiz_id, admin)
    if sent == 0 and errors == 0:
        flash("Aucun utilisateur en attente à relancer.", "info")
    elif errors:
        flash(f"{sent} rappel(s) envoyé(s). {errors} échec(s).", "error")
    else:
        flash(f"{sent} rappel(s) de quiz envoyé(s) avec succès.", "success")
    return redirect(url_for("quiz_detail", quiz_id=quiz_id))


@app.route("/quizzes/<int:quiz_id>/resend/<int:attempt_id>", methods=["POST"])
@login_required
def quiz_resend_individual(quiz_id, attempt_id):
    ok, msg = models.resend_quiz_individual(attempt_id)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("quiz_detail", quiz_id=quiz_id))


@app.route("/quizzes/<int:quiz_id>/toggle", methods=["POST"])
@login_required
def quiz_toggle(quiz_id):
    db.toggle_quiz_actif(quiz_id)
    return redirect(url_for("quiz_detail", quiz_id=quiz_id))


@app.route("/quizzes/<int:quiz_id>/delete", methods=["POST"])
@login_required
def quiz_delete(quiz_id):
    ok, err = db.delete_quiz(quiz_id)
    if ok:
        flash("Quiz supprimé.", "success")
        return redirect(url_for("quiz_list"))
    flash(f"Impossible de supprimer : {err}", "error")
    return redirect(url_for("quiz_detail", quiz_id=quiz_id))


# ─── Quiz — Public (accès par token) ──────────────────────────────────────────

@app.route("/quiz/<token>")
def quiz_take(token):
    attempt = db.get_quiz_attempt_by_token(token)
    if not attempt:
        return render_template("quiz_invalid.html", msg="Lien invalide ou expiré.",
                               company_name=_get_company_name()), 404
    if attempt["status"] == "completed":
        return redirect(url_for("quiz_result", token=token))

    quiz = db.get_quiz_by_id(attempt["quiz_id"])
    if not quiz or not quiz["actif"]:
        return render_template("quiz_invalid.html", msg="Ce quiz n'est plus disponible.",
                               company_name=_get_company_name()), 410

    if quiz["date_limite"]:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        if _dt.now(_ZI("Europe/Paris")).strftime("%Y-%m-%d") > quiz["date_limite"]:
            db.complete_quiz_attempt(token, 0, 0, 0, 0)
            return render_template("quiz_invalid.html", msg="Ce quiz est expiré.",
                                   company_name=_get_company_name()), 410

    # Anti-triche : 30 min max après ouverture du quiz
    QUIZ_TIMEOUT_MINUTES = 30
    if attempt["started_at"]:
        from datetime import datetime as _dt2
        started = _dt2.strptime(attempt["started_at"], "%Y-%m-%d %H:%M:%S")
        from zoneinfo import ZoneInfo as _ZI2
        now = _dt2.now(_ZI2("Europe/Paris")).replace(tzinfo=None)
        elapsed = (now - started).total_seconds() / 60
        if elapsed > QUIZ_TIMEOUT_MINUTES:
            db.complete_quiz_attempt(token, 0, 0, 0, 0)
            return render_template("quiz_invalid.html",
                                   msg="Temps écoulé — vous avez dépassé les 30 minutes.",
                                   company_name=_get_company_name()), 410

    db.start_quiz_attempt(token)
    questions = db.get_quiz_questions(quiz["id"])
    return render_template("quiz_take.html",
                           quiz=quiz, questions=questions, token=token,
                           attempt=attempt,
                           company_name=_get_company_name())


@app.route("/quiz/<token>/submit", methods=["POST"])
@csrf.exempt
def quiz_submit(token):
    attempt = db.get_quiz_attempt_by_token(token)
    if not attempt or attempt["status"] == "completed":
        return redirect(url_for("quiz_result", token=token))

    # Anti-triche : bloquer la soumission après 30 min
    QUIZ_TIMEOUT_MINUTES = 30
    if attempt.get("started_at"):
        from datetime import datetime as _dt3
        from zoneinfo import ZoneInfo as _ZI3
        started = _dt3.strptime(attempt["started_at"], "%Y-%m-%d %H:%M:%S")
        now = _dt3.now(_ZI3("Europe/Paris")).replace(tzinfo=None)
        if (now - started).total_seconds() / 60 > QUIZ_TIMEOUT_MINUTES:
            db.complete_quiz_attempt(token, 0, 0, 0, 0)
            return render_template("quiz_invalid.html",
                                   msg="Temps écoulé — vous avez dépassé les 30 minutes.",
                                   company_name=_get_company_name()), 410

    answers = {key[2:]: request.form.getlist(key) for key in request.form if key.startswith("q_")}
    result  = models.submit_quiz(token, answers)
    if result is None:
        flash("Erreur lors de la soumission.", "error")
        return redirect(url_for("quiz_take", token=token))
    return redirect(url_for("quiz_result", token=token))


@app.route("/quiz/<token>/result")
def quiz_result(token):
    attempt = db.get_quiz_attempt_by_token(token)
    if not attempt:
        return render_template("quiz_invalid.html", msg="Lien invalide.",
                               company_name=_get_company_name()), 404
    quiz         = db.get_quiz_by_id(attempt["quiz_id"])
    questions    = db.get_quiz_questions(attempt["quiz_id"])
    user_answers = db.get_attempt_answers(attempt["id"])  # {question_id: set(choice_ids)}
    return render_template("quiz_result.html",
                           attempt=attempt, quiz=quiz,
                           company_name=_get_company_name(),
                           leaderboard_url=config.LEADERBOARD_URL,
                           questions=questions,
                           user_answers=user_answers)


# ─── Formations — Admin ──────────────────────────────────────────────────────

@app.route("/formations")
@login_required
def formation_list():
    formations = db.get_all_formations()
    return render_template("formation_list.html", formations=formations)


@app.route("/formations/add", methods=["GET", "POST"])
@login_required
def formation_add():
    if request.method == "POST":
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        mois        = request.form.get("mois", "").strip()
        admin       = session.get("admin_user", "admin")

        errors = []
        if not title:
            errors.append("Le titre est obligatoire.")
        if not mois:
            errors.append("Le mois est obligatoire.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("formation_add.html", form=request.form)

        fid = db.create_formation(title, description, mois, admin)
        flash(f"Formation « {title} » créée.", "success")
        return redirect(url_for("formation_detail", formation_id=fid))

    return render_template("formation_add.html", form={})


@app.route("/formations/<int:formation_id>")
@login_required
def formation_detail(formation_id):
    formation = db.get_formation_by_id(formation_id)
    if not formation:
        flash("Formation introuvable.", "error")
        return redirect(url_for("formation_list"))
    resources = db.get_formation_resources(formation_id)
    return render_template("formation_detail.html",
                           formation=formation, resources=resources)


@app.route("/formations/<int:formation_id>/edit", methods=["GET", "POST"])
@login_required
def formation_edit(formation_id):
    formation = db.get_formation_by_id(formation_id)
    if not formation:
        flash("Formation introuvable.", "error")
        return redirect(url_for("formation_list"))

    if request.method == "POST":
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        mois        = request.form.get("mois", "").strip()

        errors = []
        if not title:
            errors.append("Le titre est obligatoire.")
        if not mois:
            errors.append("Le mois est obligatoire.")

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.update_formation(formation_id, title, description, mois)
            flash("Formation mise à jour.", "success")
            return redirect(url_for("formation_detail", formation_id=formation_id))

    return render_template("formation_edit.html", formation=formation)


@app.route("/formations/<int:formation_id>/toggle", methods=["POST"])
@login_required
def formation_toggle(formation_id):
    db.toggle_formation_actif(formation_id)
    return redirect(url_for("formation_detail", formation_id=formation_id))


@app.route("/formations/<int:formation_id>/delete", methods=["POST"])
@login_required
def formation_delete(formation_id):
    # Supprimer aussi les fichiers uploadés
    resources = db.get_formation_resources(formation_id)
    for r in resources:
        if r["resource_type"] == "file" and r["file_path"]:
            fpath = os.path.join(UPLOAD_FOLDER, r["file_path"])
            if os.path.exists(fpath):
                os.remove(fpath)
    db.delete_formation(formation_id)
    flash("Formation supprimée.", "success")
    return redirect(url_for("formation_list"))


@app.route("/formations/<int:formation_id>/resource/add", methods=["POST"])
@login_required
def formation_resource_add(formation_id):
    formation = db.get_formation_by_id(formation_id)
    if not formation:
        flash("Formation introuvable.", "error")
        return redirect(url_for("formation_list"))

    res_type    = request.form.get("resource_type", "link")
    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    url_val     = request.form.get("url", "").strip()

    if not title:
        flash("Le titre de la ressource est obligatoire.", "error")
        return redirect(url_for("formation_detail", formation_id=formation_id))

    if res_type == "link":
        if not url_val:
            flash("L'URL est obligatoire pour un lien.", "error")
            return redirect(url_for("formation_detail", formation_id=formation_id))
        db.add_formation_resource(formation_id, title, "link",
                                  url=url_val, description=description)
        flash(f"Lien « {title} » ajouté.", "success")

    elif res_type == "file":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Veuillez sélectionner un fichier.", "error")
            return redirect(url_for("formation_detail", formation_id=formation_id))

        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            flash(f"Extension « .{ext} » non autorisée.", "error")
            return redirect(url_for("formation_detail", formation_id=formation_id))

        # Créer le dossier uploads si nécessaire
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)

        # Nom de fichier sécurisé et unique
        from werkzeug.utils import secure_filename
        import secrets as _secrets
        safe_name = secure_filename(f.filename)
        unique_name = f"{_secrets.token_hex(8)}_{safe_name}"
        f.save(os.path.join(UPLOAD_FOLDER, unique_name))

        db.add_formation_resource(formation_id, title, "file",
                                  file_path=unique_name, file_name=f.filename,
                                  description=description)
        flash(f"Fichier « {title} » uploadé.", "success")

    return redirect(url_for("formation_detail", formation_id=formation_id))


@app.route("/formations/<int:formation_id>/resource/<int:resource_id>/delete", methods=["POST"])
@login_required
def formation_resource_delete(formation_id, resource_id):
    res = db.get_formation_resource_by_id(resource_id)
    if res and res["resource_type"] == "file" and res["file_path"]:
        fpath = os.path.join(UPLOAD_FOLDER, res["file_path"])
        if os.path.exists(fpath):
            os.remove(fpath)
    db.delete_formation_resource(resource_id)
    flash("Ressource supprimée.", "success")
    return redirect(url_for("formation_detail", formation_id=formation_id))


# ─── Formations — Public ────────────────────────────────────────────────────

@app.route("/formations/public")
def formations_public():
    """Page publique listant les formations et leurs ressources."""
    formations = db.get_active_formations()
    # Charger les ressources pour chaque formation
    for f in formations:
        f["resources"] = db.get_formation_resources(f["id"])
    return render_template("formations_public.html",
                           formations=formations,
                           company_name=_get_company_name(),
                           leaderboard_url=config.LEADERBOARD_URL)


@app.route("/formations/download/<int:resource_id>")
def formation_download(resource_id):
    """Télécharge une ressource fichier."""
    res = db.get_formation_resource_by_id(resource_id)
    if not res or res["resource_type"] != "file" or not res["file_path"]:
        flash("Ressource introuvable.", "error")
        return redirect(url_for("formations_public"))
    from flask import send_from_directory
    return send_from_directory(UPLOAD_FOLDER, res["file_path"],
                               as_attachment=True,
                               download_name=res["file_name"] or res["file_path"])


# ─── Page règles (publique) ───────────────────────────────────────────────────

@app.route("/regles")
def regles():
    event_types = db.get_all_event_types()
    bonus  = [e for e in event_types if e["direction"] == "+"]
    malus  = [e for e in event_types if e["direction"] == "-"]
    return render_template("regles.html",
                           bonus=bonus, malus=malus,
                           company_name=_get_company_name(),
                           leaderboard_url=config.LEADERBOARD_URL)


# ─── Lancement ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
