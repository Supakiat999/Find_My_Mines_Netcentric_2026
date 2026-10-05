# Competitive Elo Rating System

This document specifies the architecture, mathematics, and gameplay mechanics of the **Elo Rating System** in *Find My Mines*.

---

## 1. Overview

The Elo system provides skill-based matchmaking ratings and competitive progression. It features:
* **Per-mode rating isolation** (Classic, Radius 2, Minesweeper, 3D Cube).
* **6 competitive rank tiers** from Bronze to Master (2000+).
* **Accelerated provisional calibration** ($K = 64$ for the first 5 matches).
* **Win streak bonuses** ($+6$ Elo for streaks of 3 or more).
* **All-time high (Peak Elo)** tracking per mode.
* **Ranked vs. Casual mode toggle** with anti-exploitation match restart.
* **Animated rating ticker** with promotion alerts on match end.

---

## 2. Mathematical Model

### Expected Score Formula
For two players with ratings $R_A$ and $R_B$, the expected outcome $E_A$ and $E_B$ are calculated using the standard logistic distribution:

$$E_A = \frac{1}{1 + 10^{(R_B - R_A) / 400}}$$

$$E_B = \frac{1}{1 + 10^{(R_A - R_B) / 400}}$$

### Rating Updates
After a match, the actual outcome $S$ is evaluated:
* $S = 1.0$ (Win)
* $S = 0.5$ (Draw)
* $S = 0.0$ (Loss)

The rating delta for player $A$ is:

$$\Delta R_A = \text{round}(K_A \times (S_A - E_A))$$

The new rating is:

$$R'_A = \max(R_{\min}, R_A + \Delta R_A)$$

where $R_{\min} = 100$ is the rating floor to prevent ratings from dropping below 100.

---

## 3. Placement & Progression Mechanics

### Dynamic K-Factor (Provisional Calibration)
To quickly place new players at their true skill level:
* **Matches 1 to 5** in a given mode: $K = 64$ (Provisional calibration).
* **Matches 6+**: $K = 32$ (Standard competitive factor).

During calibration, the in-game scoreboard displays `[Calibrating X/5]` next to the player's name. Because players can have different match counts, each player's delta is calculated using their own personalized $K$-factor.

### Win Streak Bonus
To reward dominant play and move high-skill players out of lower brackets faster:
* When a player reaches a win streak of $\ge 3$ in a ranked match, they receive a flat **$+6$ Elo bonus**:

$$\Delta R_{\text{winner}} = \Delta R_{\text{standard}} + 6$$

The post-match screen and room chat highlight the bonus: `+22 ELO (+6 streak!)`.

---

## 4. Rank Tiers & Visual Badges

Players are categorized into 6 competitive tiers based on their current rating in each mode:

| Tier | Elo Range | Color (RGB) | Visual Badge |
|---|---|---|---|
| **Bronze** | $0 - 1099$ | `(195, 130, 80)` | Warm Bronze |
| **Silver** | $1100 - 1299$ | `(185, 195, 205)` | Bright Silver |
| **Gold** | $1300 - 1499$ | `(240, 185, 50)` | Radiant Gold |
| **Platinum** | $1500 - 1699$ | `(65, 215, 195)` | Cyan Platinum |
| **Diamond** | $1700 - 1999$ | `(170, 130, 250)` | Violet Diamond |
| **Master** | $2000+$ | `(255, 80, 120)` | Crimson Master |

### Tier Promotions
When a victory pushes a player across a tier boundary:
* The post-match screen displays `▲ PROMOTED TO [TIER]!`.
* A system announcement is broadcast to all clients in the room chat.

---

## 5. Peak Elo Tracking (All-Time High)

Every player's highest rating achieved in each mode is permanently tracked as **Peak Elo**:
* Automatically updated when current Elo surpasses the previous peak:
  $$\text{Peak}' = \max(\text{Peak}, R')$$
* Displayed on the **Hall of Fame** sidebar card:
  `SILV 1231 (Pk 1250)  2W 1L`
* Saved to disk in `stats.json` under each player's `"peak_elo"` map.

---

## 6. Per-Mode Rating Isolation

Each supported game mode maintains its own independent Elo rating pool:

| Game Mode | Ranked Support | Starting Rating |
|---|---|---|
| **Classic** (6×6, 11 bombs) | Yes | 1200 |
| **Radius 2** (8×8, 16 bombs, Chebyshev dist 2) | Yes | 1200 |
| **Minesweeper** (9×9, 10 bombs, avoid bombs) | Yes | 1200 |
| **3D Cube** (3×3×3, 5 bombs) | Yes | 1200 |
| **Custom** (user-defined rules/dimensions) | No (Always Casual) | — |

A player's ranking in *Classic* does not influence their rating in *Minesweeper* or *3D Cube*.

---

## 7. Ranked vs. Casual Mode

### Client Toggle Button
Any connected player can click the **[Ranked / Casual]** button in the titlebar (top left):
* **Ranked Mode (Default):** Matches update Elo ratings, win streaks, peak ratings, and leaderboard rankings.
* **Casual Mode:** Matches record basic win/loss tallies and points, but Elo ratings are frozen.

### Anti-Exploitation Mid-Game Restart
To prevent mid-match rating manipulation (e.g., switching to Casual when losing to avoid losing Elo, or switching to Ranked when ahead):
* If the mode is toggled while a match is actively in progress (`phase != PHASE_WAITING`):
  1. Active match scores are wiped.
  2. The server broadcasts a `SERVER_RESET` packet.
  3. A room announcement explains: `"Match restarted - match mode changed to [Ranked | Casual]"`.
  4. A fresh board is immediately dealt for a new match under the updated mode.

### Solo Play vs. Computer
Matches played against the AI bot are always unranked. The computer never gains or loses Elo and is never listed in the Hall of Fame.

---

## 8. Animated Rating Ticker

When a ranked match concludes:
* An ease-out interpolation animates the displayed rating counting smoothly from the previous rating to the new rating over 1.0 second:
  $$\text{display}(t) = R_{\text{old}} + (R_{\text{new}} - R_{\text{old}}) \times (1 - (1 - t)^3)$$
* Once the animation completes, promotion tags (`▲ GOLD!`) and streak bonus highlights are shown.

---

## 9. File Persistence & Schema

Ratings are stored on the server in `stats.json`:

```json
{
  "Phut": {
    "wins": 2,
    "losses": 1,
    "draws": 0,
    "points": 23,
    "matches": 4,
    "streak": 0,
    "best_streak": 1,
    "elo": {
      "classic": 1193,
      "radius2": 1200,
      "sweeper": 1200,
      "cube": 1200
    },
    "peak_elo": {
      "classic": 1231,
      "radius2": 1200,
      "sweeper": 1200,
      "cube": 1200
    },
    "mode_matches": {
      "classic": 2,
      "radius2": 0,
      "sweeper": 0,
      "cube": 0
    }
  }
}
```

*Legacy migration:* Files from older versions without Elo data are automatically migrated upon loading, initializing starting ratings to 1200 and setting initial peaks to match current ratings.

---

## 10. Configuration Reference

All Elo parameters are configurable in [config.py](config.py):

| Constant | Default Value | Description |
|---|---|---|
| `ELO_STARTING` | `1200` | Baseline rating for all players in each ranked mode |
| `ELO_K_FACTOR` | `32` | Standard K-factor for established players |
| `ELO_PROVISIONAL_K` | `64` | Accelerated K-factor for placement matches |
| `ELO_PROVISIONAL_MATCHES` | `5` | Number of placement matches per mode |
| `ELO_STREAK_THRESHOLD` | `3` | Consecutive wins required to activate streak bonus |
| `ELO_STREAK_BONUS` | `6` | Flat bonus Elo awarded per match while on a streak |
| `ELO_MINIMUM` | `100` | Lowest allowable Elo rating |
| `ELO_RANKED_MODES` | `("classic", "radius2", "sweeper", "cube")` | Modes with ranked matchmaking enabled |
| `ELO_TIERS` | 6 tiers | Tuples of `(name, min_elo, rgb_color)` |
