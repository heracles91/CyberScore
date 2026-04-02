# models.py — Logique métier CyberScore

import database as db
import mailer


def calc_niveau(score):
    """Retourne le niveau correspondant au score."""
    if score >= 600:
        return "Expert"
    elif score >= 300:
        return "Gardien"
    elif score >= 100:
        return "Vigilant"
    else:
        return "Débutant"


def add_event(user_id, type_id, points_override=None, raison="", created_by="admin"):
    """
    Ajoute un événement pour un utilisateur :
    - Calcule le nouveau score (plancher 0)
    - Met à jour le niveau
    - Envoie un email de notification (erreur loggée, non bloquante)

    Retourne (ok, message, event_id)
    """
    user = db.get_user_by_id(user_id)
    if not user:
        return False, "Utilisateur introuvable.", None

    etype = db.get_event_type_by_id(type_id)
    if not etype:
        return False, "Type d'événement introuvable.", None

    # Vérification unicité COFFRE_MDP
    if etype["code"] == "COFFRE_MDP" and db.user_has_coffre_mdp(user_id):
        return False, "COFFRE_MDP déjà attribué à cet utilisateur.", None

    # Points effectifs
    points = points_override if points_override is not None else etype["points"]

    # Calcul nouveau score
    score_avant = user["score_total"]
    niveau_avant = user["niveau"]
    new_score = max(0, score_avant + points)
    new_niveau = calc_niveau(new_score)

    # Insertion événement
    event_id = db.insert_event(user_id, type_id, points, raison, created_by)

    # Mise à jour utilisateur
    db.update_user_score(user_id, new_score, new_niveau)

    # Notification email (non bloquante)
    notif_id = db.insert_notification(event_id, user_id)
    try:
        mailer.send_event_notification(
            user=user,
            event_id=event_id,
            etype=etype,
            points=points,
            score_avant=score_avant,
            score_apres=new_score,
            niveau_avant=niveau_avant,
            niveau_apres=new_niveau,
            raison=raison,
            annulation=False,
        )
        db.update_notification_status(notif_id, "sent")
    except Exception as e:
        db.update_notification_status(notif_id, "error", str(e))

    return True, "Événement enregistré avec succès.", event_id


def cancel_event(event_id, admin_login="admin"):
    """
    Annule un événement :
    - Marque l'événement annule=1
    - Recalcule le score depuis zéro
    - Envoie un email d'annulation

    Retourne (ok, message)
    """
    event = db.get_event_by_id(event_id)
    if not event:
        return False, "Événement introuvable."
    if event["annule"]:
        return False, "Événement déjà annulé."

    user_id = event["user_id"]
    user = db.get_user_by_id(user_id)
    etype = db.get_event_type_by_id(event["type_id"])

    score_avant = user["score_total"]
    niveau_avant = user["niveau"]

    # Annulation
    db.annuler_event(event_id)

    # Recalcul depuis zéro (somme de tous les events non annulés)
    new_score = db.recalculate_user_score(user_id)
    new_niveau = calc_niveau(new_score)
    db.update_user_score(user_id, new_score, new_niveau)

    # Notification annulation (non bloquante)
    notif_id = db.insert_notification(event_id, user_id)
    try:
        mailer.send_event_notification(
            user=user,
            event_id=event_id,
            etype=etype,
            points=event["points"],
            score_avant=score_avant,
            score_apres=new_score,
            niveau_avant=niveau_avant,
            niveau_apres=new_niveau,
            raison=event["raison"] or "",
            annulation=True,
        )
        db.update_notification_status(notif_id, "sent")
    except Exception as e:
        db.update_notification_status(notif_id, "error", str(e))

    return True, "Événement annulé. Score recalculé."


def submit_quiz(token, answers):
    """
    Corrige un quiz soumis par un utilisateur.
    answers : dict { str(question_id): list[str(choice_id)] }
    Retourne (score_pct, nb_correct, nb_total, reussi, points_awarded) ou None si invalide.
    """
    attempt = db.get_quiz_attempt_by_token(token)
    if not attempt or attempt["status"] == "completed":
        return None

    quiz      = db.get_quiz_by_id(attempt["quiz_id"])
    questions = db.get_quiz_questions(attempt["quiz_id"])
    nb_total  = len(questions)

    if nb_total == 0:
        return None

    nb_correct = 0
    answer_pairs = []
    for q in questions:
        chosen_ids = set(answers.get(str(q["id"]), []))
        correct_ids = {str(c["id"]) for c in q["choices"] if c["is_correct"]}
        if q.get("multiple_answers"):
            if chosen_ids == correct_ids:
                nb_correct += 1
        else:
            if chosen_ids & correct_ids:
                nb_correct += 1
        for cid in chosen_ids:
            try:
                answer_pairs.append((q["id"], int(cid)))
            except (ValueError, TypeError):
                pass

    # Stocker les réponses sélectionnées pour affichage de la correction
    db.save_attempt_answers(attempt["id"], answer_pairs)

    score_pct = int((nb_correct / nb_total) * 100)
    reussi    = score_pct >= quiz["seuil_reussite"]
    is_test   = bool(attempt.get("is_test", 0))

    points_awarded = 0
    if reussi and not is_test:
        etype = db.get_event_type_by_code("QUIZ_REUSSI")
        if etype:
            ok, _, _ = add_event(
                attempt["user_id"],
                etype["id"],
                None,
                f"Quiz « {quiz['title']} » — Score : {score_pct}%",
                "quiz_system",
            )
            if ok:
                points_awarded = etype["points"]

    db.complete_quiz_attempt(token, score_pct, nb_correct, nb_total, points_awarded)
    return score_pct, nb_correct, nb_total, reussi, points_awarded


def send_quiz_invitations(quiz_id, admin_login):
    """
    Crée une tentative + envoie un email d'invitation à chaque utilisateur actif
    sans tentative existante pour ce quiz.
    Retourne (nb_sent, nb_errors).
    """
    import mailer as _mailer
    quiz  = db.get_quiz_by_id(quiz_id)
    users = db.get_users_without_quiz_attempt(quiz_id)

    sent = 0
    errors = 0
    for user in users:
        token = db.create_quiz_attempt(quiz_id, user["id"])
        try:
            _mailer.send_quiz_invitation(user, quiz, token)
            db.mark_quiz_attempt_sent(token)
            sent += 1
        except Exception:
            errors += 1

    return sent, errors


def send_quiz_reminders(quiz_id, admin_login):
    """
    Renvoie un email de rappel aux utilisateurs ayant une tentative pending (non complétée).
    Retourne (nb_sent, nb_errors).
    """
    import mailer as _mailer
    quiz    = db.get_quiz_by_id(quiz_id)
    pending = db.get_pending_attempts_with_users(quiz_id)

    sent = 0
    errors = 0
    for attempt in pending:
        user = {"nom": attempt["nom"], "prenom": attempt["prenom"], "email": attempt["email"]}
        try:
            _mailer.send_quiz_invitation(user, quiz, attempt["token"])
            sent += 1
        except Exception:
            errors += 1

    return sent, errors


def get_leaderboard_with_progression():
    """
    Retourne le leaderboard enrichi de la progression vs hier.
    Format: liste de dicts avec rang, nom, prenom, score_total, niveau, delta_rang, delta_score
    """
    users = db.get_leaderboard()
    result = []
    for rang, u in enumerate(users, 1):
        hier = db.get_yesterday_rank(u["id"])
        if hier:
            delta_rang = hier["rang"] - rang    # positif = montée
            delta_score = u["score_total"] - hier["score"]
        else:
            delta_rang = None
            delta_score = None
        result.append({
            **u,
            "rang": rang,
            "delta_rang": delta_rang,
            "delta_score": delta_score,
        })
    return result
