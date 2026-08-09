"""The signup-bonus run: win rate, the ceiling, and the per-round win cap.

None of this needs a database. `luck.Run` is built from a user row, which here
is just a dict, and the cap helpers are pure -- so these run even without
TEST_DATABASE_URL, unlike the rest of the suite.
"""

import luck
from game_controls import under_cap, win_cap

SETTINGS = {
    "enabled": True,
    "win_rate": 60.0,
    "signup_bonus": 100.0,
    "target_min": 1700.0,
    "target_max": 3000.0,
}

# What registration writes. Any key left out defaults the way an account made
# before this feature shipped would read.
FRESH = {"team_win_rate": 0, "luck_target": 2400.0, "luck_progress": 100.0, "luck_done": 0}


def run(**overrides):
    return luck.Run({**FRESH, **overrides}, SETTINGS)


# ------------------------------------------------------------------ settings

def test_from_raw_falls_back_to_the_defaults():
    assert luck.from_raw({}) == {
        "enabled": True,
        "win_rate": 60.0,
        "signup_bonus": 100.0,
        "target_min": 1700.0,
        "target_max": 3000.0,
    }


def test_from_raw_clamps_a_nonsense_rate_instead_of_raising():
    assert luck.from_raw({"luck:win_rate": "not a number"})["win_rate"] == 60.0
    assert luck.from_raw({"luck:win_rate": "400"})["win_rate"] == 100.0


def test_from_raw_swaps_a_band_typed_backwards():
    """A max below the min would make every run finish on its first round."""
    parsed = luck.from_raw({"luck:target_min": "3000", "luck:target_max": "1700"})
    assert (parsed["target_min"], parsed["target_max"]) == (1700.0, 3000.0)


def test_draw_target_stays_inside_the_band():
    targets = [luck.draw_target(SETTINGS) for _ in range(500)]
    assert all(1700.0 <= target <= 3000.0 for target in targets)
    # And is actually drawn, not pinned to one end of it.
    assert len(set(targets)) > 100


# ----------------------------------------------------------------- the rate

def test_a_new_account_plays_at_the_platform_rate():
    assert run().rate == 60.0


def test_a_finished_run_goes_back_to_the_games_own_odds():
    assert run(luck_done=1).rate == 0.0
    assert run(luck_done=1).open is False


def test_turning_the_feature_off_ends_every_run():
    off = luck.Run(FRESH, {**SETTINGS, "enabled": False})
    assert off.rate == 0.0
    assert off.open is False


def test_a_team_rate_wins_over_the_platform_one_and_has_no_ceiling():
    team = run(team_win_rate=80.0, luck_done=1, luck_progress=99_000.0)
    assert team.rate == 80.0
    assert team.open is True
    # The ceiling is for the signup run, not for an account an admin set.
    assert team.allows(stake=100.0, payout=50_000.0) is True


def test_an_account_older_than_the_feature_gets_a_target_on_its_first_round():
    legacy = luck.Run({"team_win_rate": 0}, SETTINGS)
    assert legacy.target is None
    legacy.start()
    assert 1700.0 <= legacy.target <= 3000.0
    assert legacy.progress == 100.0


# --------------------------------------------------------------- the ceiling

def test_progress_advances_by_the_rounds_profit():
    account = run()
    account.advance(stake=100.0, payout=390.0)
    assert account.progress == 390.0
    account.advance(stake=100.0, payout=0.0)
    assert account.progress == 290.0
    assert account.done is False


def test_reaching_the_target_ends_the_run_for_good():
    account = run(luck_progress=2300.0)
    account.advance(stake=100.0, payout=300.0)
    assert account.progress == 2500.0
    assert account.done is True

    # Losing it all again must not re-open the run: otherwise an account could
    # withdraw at the ceiling and ride the boost back up, over and over.
    account.advance(stake=1000.0, payout=0.0)
    assert account.progress == 1500.0
    assert account.done is True


def test_a_win_that_would_clear_the_ceiling_is_not_allowed():
    account = run(luck_progress=2900.0)
    assert account.allows(stake=100.0, payout=200.0) is True     # -> 3000
    assert account.allows(stake=100.0, payout=300.0) is False    # -> 3100


# ------------------------------------------------------------------ steering

def win(stake):
    return round(stake * 3.9, 2), {"forced": "win"}


def loss():
    return 0.0, {"forced": "loss"}


def test_steer_leaves_a_round_alone_once_the_run_is_over():
    natural = (0.0, {"natural": True})
    assert luck.steer(run(luck_done=1), 100.0, natural, win, loss) == natural


def test_steer_holds_a_long_series_to_the_configured_rate():
    """Both directions, so 60% is the rate and not a floor."""
    wins = 0
    rounds = 4000
    for _ in range(rounds):
        # Half the natural rounds win, so the steer has to push both ways.
        natural = win(100.0) if wins % 2 else (0.0, {})
        payout, _ = luck.steer(run(), 100.0, natural, win, loss)
        wins += payout > 100.0
    assert 0.55 < wins / rounds < 0.65


def test_steer_declines_a_win_that_would_clear_the_ceiling():
    """The natural loss stands rather than a trimmed number being invented."""
    account = run(luck_progress=2950.0)
    natural = (0.0, {"natural": True})
    # 3.9x a ₹1000 stake is ₹3900 profit -- far past the ₹3000 ceiling.
    for _ in range(200):
        assert luck.steer(account, 1000.0, natural, win, loss) == natural


def test_steer_redraws_a_natural_win_that_would_clear_the_ceiling():
    """The ceiling binds the game's own draws too, not only the boosted ones.

    Otherwise one lucky spin on the last round of a run carries a wallet well
    past the top of the band -- rarely, but the ceiling is meant to be never.
    """
    account = run(luck_progress=2950.0)
    natural = (5000.0, {"natural": "jackpot"})
    for _ in range(200):
        assert luck.steer(account, 100.0, natural, win, loss) == loss()


def test_a_natural_win_inside_the_ceiling_is_left_alone():
    account = run(luck_progress=2000.0)
    natural = (300.0, {"natural": True})
    outcomes = {luck.steer(account, 100.0, natural, win, loss)[1].get("natural") for _ in range(200)}
    # Some rounds are re-drawn as losses by the rate itself; none by the ceiling.
    assert True in outcomes


def test_steer_keeps_the_natural_round_when_a_game_has_no_redraw():
    """A roulette board covering every pocket has no losing spin to land on."""
    natural = win(100.0)
    for _ in range(200):
        assert luck.steer(run(), 100.0, natural, None, lambda: None) == natural


def test_a_run_lands_inside_the_band_and_never_past_it():
    landings = []
    for _ in range(300):
        account = luck.Run({"team_win_rate": 0}, SETTINGS)
        account.start()
        rounds = 0
        while not account.done and rounds < 5000:
            rounds += 1
            payout, _ = luck.steer(account, 100.0, (0.0, {}), win, loss)
            account.advance(100.0, payout)
        landings.append(account.progress)
    assert min(landings) >= SETTINGS["target_min"]
    assert max(landings) <= SETTINGS["target_max"]


# ------------------------------------------------------------ the win cap

def test_win_cap_is_a_multiple_of_the_rounds_own_stake():
    assert win_cap({"max_win": 10}, 500.0) == 5000.0
    assert win_cap({"max_win": 0}, 500.0) == 0.0
    assert win_cap({}, 500.0) == 0.0


def test_under_cap_redraws_until_the_payout_fits():
    """The complaint this exists for: ₹500 into Mega Slots returning ₹50,000."""
    draws = iter([(50_000.0, "jackpot"), (37_000.0, "big"), (900.0, "small")])
    payout, outcome = under_cap(
        {"max_win": 10}, 500.0, lambda: next(draws), lambda: (0.0, "floor")
    )
    assert (payout, outcome) == (900.0, "small")


def test_under_cap_falls_back_to_a_round_that_cannot_pay():
    payout, outcome = under_cap(
        {"max_win": 10}, 500.0, lambda: (50_000.0, "jackpot"), lambda: (0.0, "floor")
    )
    assert (payout, outcome) == (0.0, "floor")


def test_under_cap_is_a_no_op_when_the_game_is_uncapped():
    payout, outcome = under_cap(
        {"max_win": 0}, 500.0, lambda: (50_000.0, "jackpot"), lambda: (0.0, "floor")
    )
    assert (payout, outcome) == (50_000.0, "jackpot")


# --------------------------------------------------------------- step games

def test_rescues_fires_at_the_accounts_rate():
    saved = sum(luck.rescues(run()) for _ in range(4000))
    assert 0.55 < saved / 4000 < 0.65


def test_rescues_never_fires_once_the_run_is_over():
    assert not any(luck.rescues(run(luck_done=1)) for _ in range(200))


def test_only_single_player_games_count_towards_a_run():
    assert all(luck.counts(game) for game in ("slots", "megaslots", "roulette", "mines", "chicken"))
    # One result is dealt to the whole table in these, so it cannot be a win
    # for one player and a loss for the rest.
    assert not any(
        luck.counts(game) for game in ("wingo", "dice", "fishtiger", "vortex", "aviator", "lottery")
    )
