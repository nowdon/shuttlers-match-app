from models import MatchHistory, Participant


def _empty_stats():
    return {"wins": 0, "losses": 0, "games": 0, "win_rate": 0.0}


def calculate_participant_win_stats():
    """Calculate per-participant win/loss stats from recorded match results.

    Only MatchHistory rows with winner_team 1 or 2 are counted. Ties and
    unentered results (winner_team is None) are ignored, and the values are not
    persisted to the database.
    """
    stats = {participant.id: _empty_stats() for participant in Participant.query.all()}

    histories = MatchHistory.query.filter(MatchHistory.winner_team.in_((1, 2))).all()
    for history in histories:
        team1_player_ids = [history.team1_player1_id, history.team1_player2_id]
        team2_player_ids = [history.team2_player1_id, history.team2_player2_id]

        if history.winner_team == 1:
            winning_player_ids = team1_player_ids
            losing_player_ids = team2_player_ids
        else:
            winning_player_ids = team2_player_ids
            losing_player_ids = team1_player_ids

        for participant_id in winning_player_ids:
            participant_stats = stats.setdefault(participant_id, _empty_stats())
            participant_stats["wins"] += 1

        for participant_id in losing_player_ids:
            participant_stats = stats.setdefault(participant_id, _empty_stats())
            participant_stats["losses"] += 1

    for participant_stats in stats.values():
        games = participant_stats["wins"] + participant_stats["losses"]
        participant_stats["games"] = games
        participant_stats["win_rate"] = participant_stats["wins"] / games if games else 0.0

    return stats
