CREATE TABLE players (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nickname varchar(16) NOT NULL UNIQUE CHECK (length(trim(nickname)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    matches bigint NOT NULL DEFAULT 0 CHECK (matches >= 0),
    wins bigint NOT NULL DEFAULT 0 CHECK (wins >= 0),
    losses bigint NOT NULL DEFAULT 0 CHECK (losses >= 0),
    draws bigint NOT NULL DEFAULT 0 CHECK (draws >= 0),
    points bigint NOT NULL DEFAULT 0 CHECK (points >= 0),
    streak bigint NOT NULL DEFAULT 0 CHECK (streak >= 0),
    best_streak bigint NOT NULL DEFAULT 0 CHECK (best_streak >= streak),
    CHECK (matches = wins + losses + draws)
);

CREATE TABLE player_ratings (
    player_id bigint NOT NULL REFERENCES players(id),
    mode text NOT NULL CHECK (mode IN ('classic', 'radius2', 'sweeper', 'cube')),
    elo integer NOT NULL DEFAULT 1200 CHECK (elo >= 100),
    peak_elo integer NOT NULL DEFAULT 1200 CHECK (peak_elo >= elo),
    ranked_matches bigint NOT NULL DEFAULT 0 CHECK (ranked_matches >= 0),
    PRIMARY KEY (player_id, mode)
);

CREATE TABLE matches (
    id uuid PRIMARY KEY,
    mode text NOT NULL CHECK (mode IN ('classic', 'radius2', 'sweeper', 'cube', 'custom')),
    settings jsonb NOT NULL CHECK (jsonb_typeof(settings) = 'object'),
    ranked_requested boolean NOT NULL,
    rated boolean NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    input_snapshot jsonb NOT NULL CHECK (jsonb_typeof(input_snapshot) = 'object'),
    rating_changes jsonb NOT NULL CHECK (jsonb_typeof(rating_changes) = 'object'),
    CHECK (ended_at >= started_at),
    CHECK (NOT rated OR (ranked_requested AND mode <> 'custom'))
);

CREATE TABLE match_participants (
    match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    seat smallint NOT NULL CHECK (seat IN (1, 2)),
    player_id bigint REFERENCES players(id),
    nickname text NOT NULL CHECK (length(trim(nickname)) > 0),
    bot_level text CHECK (bot_level IN ('easy', 'medium', 'hard')),
    result text NOT NULL CHECK (result IN ('win', 'loss', 'draw')),
    score integer NOT NULL CHECK (score >= 0),
    picks integer NOT NULL CHECK (picks >= 0),
    hits integer NOT NULL CHECK (hits BETWEEN 0 AND picks),
    best_chain integer NOT NULL CHECK (best_chain BETWEEN 0 AND hits),
    elo_before integer CHECK (elo_before >= 100),
    elo_after integer CHECK (elo_after >= 100),
    streak_bonus integer NOT NULL DEFAULT 0 CHECK (streak_bonus >= 0),
    PRIMARY KEY (match_id, seat),
    CHECK ((player_id IS NOT NULL) <> (bot_level IS NOT NULL)),
    CHECK (player_id IS NULL OR length(nickname) <= 16),
    CHECK ((elo_before IS NULL) = (elo_after IS NULL)),
    CHECK (bot_level IS NULL OR elo_before IS NULL)
);

CREATE UNIQUE INDEX match_humans ON match_participants (match_id, player_id)
    WHERE player_id IS NOT NULL;
CREATE INDEX ratings_leaderboard ON player_ratings (mode, elo DESC);
CREATE INDEX participant_history ON match_participants (player_id, match_id);
CREATE INDEX matches_recent ON matches (ended_at DESC);
