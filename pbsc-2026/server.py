import http.server
import socketserver
import json
import sqlite3
import os
import sys
import urllib.parse
from pathlib import Path

PORT = 8000
DB_FILE = os.path.join(os.path.dirname(__file__), "pbsc.db")
STATIC_DIR = os.path.dirname(__file__)

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Teams table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#2563EB',
            captain_id INTEGER DEFAULT NULL,
            captain_name TEXT DEFAULT ''
        )
    ''')
    
    # Players table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            team_id INTEGER,
            tier TEXT NOT NULL CHECK(tier IN ('A1', 'A2', 'B', 'C1', 'C2')),
            power_point INTEGER NOT NULL,
            is_late INTEGER DEFAULT 0,
            FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL
        )
    ''')
    
    # Ties table (Team vs Team match tie)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL CHECK(stage IN ('Group Stage', 'Playoff')),
            round_name TEXT NOT NULL,
            match_num INTEGER NOT NULL,
            team1_id INTEGER,
            team2_id INTEGER,
            score1 INTEGER DEFAULT 0,
            score2 INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Scheduled' CHECK(status IN ('Scheduled', 'Live', 'Completed')),
            winner_team_id INTEGER,
            FOREIGN KEY (team1_id) REFERENCES teams (id) ON DELETE CASCADE,
            FOREIGN KEY (team2_id) REFERENCES teams (id) ON DELETE CASCADE
        )
    ''')
    
    # Matches table (Partai Ganda 1, 2, 3 inside a tie)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tie_id INTEGER NOT NULL,
            partai_num INTEGER NOT NULL CHECK(partai_num IN (1, 2, 3)),
            t1_p1_id INTEGER,
            t1_p2_id INTEGER,
            t2_p1_id INTEGER,
            t2_p2_id INTEGER,
            pair1_points INTEGER DEFAULT 0,
            pair2_points INTEGER DEFAULT 0,
            t1_penalty INTEGER DEFAULT 0,
            t2_penalty INTEGER DEFAULT 0,
            t1_score INTEGER DEFAULT 0,
            t2_score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Scheduled' CHECK(status IN ('Scheduled', 'Live', 'Completed')),
            winner_team_id INTEGER,
            FOREIGN KEY (tie_id) REFERENCES ties (id) ON DELETE CASCADE
        )
    ''')
    
    # Settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('tournament_name', 'PBSC Internal Tournament 2026 - 4th Anniversary')")
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('club_name', 'PBSC')")
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('tagline', 'Internal Tournament')")
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('late_penalty_points', '5')")
    
    conn.commit()
    conn.close()
    
    ensure_default_ties()

def ensure_default_ties():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM ties")
    row = cursor.fetchone()
    if row['cnt'] == 0:
        cursor.execute("SELECT id FROM teams ORDER BY id ASC LIMIT 4")
        teams = [r['id'] for r in cursor.fetchall()]
        t1 = teams[0] if len(teams) > 0 else None
        t2 = teams[1] if len(teams) > 1 else None
        t3 = teams[2] if len(teams) > 2 else None
        t4 = teams[3] if len(teams) > 3 else None

        schedule = [
            ("Group Stage", "Match 1", 1, t1, t2),
            ("Group Stage", "Match 2", 2, t3, t4),
            ("Group Stage", "Match 3", 3, t1, t3),
            ("Group Stage", "Match 4", 4, t2, t4),
            ("Group Stage", "Match 5", 5, t1, t4),
            ("Group Stage", "Match 6", 6, t4, t3),
            ("Playoff", "Final Juara 1", 7, None, None),
            ("Playoff", "Perebutan Juara 3", 8, None, None),
        ]
        for stage, round_name, match_num, team1_id, team2_id in schedule:
            cursor.execute(
                "INSERT INTO ties (stage, round_name, match_num, team1_id, team2_id) VALUES (?, ?, ?, ?, ?)",
                (stage, round_name, match_num, team1_id, team2_id)
            )
            tie_id = cursor.lastrowid
            for p_num in (1, 2, 3):
                cursor.execute(
                    "INSERT INTO matches (tie_id, partai_num) VALUES (?, ?)",
                    (tie_id, p_num)
                )
        conn.commit()
    conn.close()

TIER_POINTS = {
    'A1': 5,
    'A2': 4,
    'B': 3,
    'C1': 2,
    'C2': 1
}

def recalculate_standings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM teams")
    teams = [dict(t) for t in cursor.fetchall()]
    
    standings = []
    for team in teams:
        t_id = team['id']
        cursor.execute("SELECT * FROM ties WHERE (team1_id = ? OR team2_id = ?) AND status = 'Completed'", (t_id, t_id))
        completed_ties = [dict(r) for r in cursor.fetchall()]
        
        played = len(completed_ties)
        wins = 0
        losses = 0
        partai_won = 0
        partai_lost = 0
        points_for = 0
        points_against = 0
        
        for tie in completed_ties:
            if tie['winner_team_id'] == t_id:
                wins += 1
            else:
                losses += 1
                
            cursor.execute("SELECT * FROM matches WHERE tie_id = ?", (tie['id'],))
            tie_matches = [dict(m) for m in cursor.fetchall()]
            for m in tie_matches:
                if tie['team1_id'] == t_id:
                    partai_won += 1 if m['winner_team_id'] == t_id else 0
                    partai_lost += 1 if m['winner_team_id'] and m['winner_team_id'] != t_id else 0
                    points_for += m['t1_score']
                    points_against += m['t2_score']
                else:
                    partai_won += 1 if m['winner_team_id'] == t_id else 0
                    partai_lost += 1 if m['winner_team_id'] and m['winner_team_id'] != t_id else 0
                    points_for += m['t2_score']
                    points_against += m['t1_score']
                    
        standings.append({
            'team': team,
            'played': played,
            'wins': wins,
            'losses': losses,
            'partai_won': partai_won,
            'partai_lost': partai_lost,
            'partai_diff': partai_won - partai_lost,
            'points_for': points_for,
            'points_against': points_against,
            'points_diff': points_for - points_against
        })
        
    standings.sort(key=lambda x: (x['wins'], x['partai_diff'], x['points_diff'], x['points_for']), reverse=True)
    conn.close()
    return standings

def update_playoff_teams():
    standings = recalculate_standings()
    if len(standings) >= 4:
        top1_id = standings[0]['team']['id']
        top2_id = standings[1]['team']['id']
        top3_id = standings[2]['team']['id']
        top4_id = standings[3]['team']['id']
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE round_name = 'Final Juara 1'", (top1_id, top2_id))
        cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE round_name = 'Perebutan Juara 3'", (top3_id, top4_id))
        conn.commit()
        conn.close()

def sort_pairings_for_team(player_ids):
    by_tier = {}
    for p in player_ids:
        t = p['tier']
        by_tier.setdefault(t, []).append(p)
        
    pairs = []
    a1 = by_tier.get('A1', [None])[0]
    a2 = by_tier.get('A2', [None])[0]
    bs = by_tier.get('B', [None, None])
    b1 = bs[0] if len(bs) > 0 else None
    b2 = bs[1] if len(bs) > 1 else None
    c1 = by_tier.get('C1', [None])[0]
    c2 = by_tier.get('C2', [None])[0]
    
    if a1 and c1 and a2 and b1 and b2 and c2:
        pair1 = [a1, c1]
        pair2 = [a2, b1]
        pair3 = [b2, c2]
        raw_pairs = [pair1, pair2, pair3]
    else:
        sorted_players = sorted(player_ids, key=lambda x: x['power_point'], reverse=True)
        raw_pairs = []
        l = len(sorted_players)
        if l >= 6:
            raw_pairs = [
                [sorted_players[0], sorted_players[4]],
                [sorted_players[1], sorted_players[3]],
                [sorted_players[2], sorted_players[5]]
            ]
        elif l >= 2:
            for i in range(0, l - (l % 2), 2):
                raw_pairs.append([sorted_players[i], sorted_players[i+1]])
                
    def pair_key(pair):
        pts = sum(p['power_point'] for p in pair if p)
        max_p = max((p['power_point'] for p in pair if p), default=0)
        return (pts, max_p)
        
    sorted_pairs = sorted(raw_pairs, key=pair_key, reverse=True)
    return sorted_pairs

class PBSCHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self.handle_api_get(parsed)
        else:
            if parsed.path == '/' or parsed.path == '/index.html':
                self.send_file(os.path.join(STATIC_DIR, 'index.html'), 'text/html')
            elif parsed.path.endswith('.jpg') or parsed.path.endswith('.jpeg'):
                file_path = os.path.join(STATIC_DIR, parsed.path.lstrip('/'))
                if os.path.exists(file_path):
                    self.send_file(file_path, 'image/jpeg')
                else:
                    self.send_error(404, 'File Not Found')
            elif parsed.path.endswith('.png'):
                file_path = os.path.join(STATIC_DIR, parsed.path.lstrip('/'))
                if os.path.exists(file_path):
                    self.send_file(file_path, 'image/png')
                else:
                    self.send_error(404, 'File Not Found')
            else:
                super().do_GET()

    def send_file(self, file_path, content_type):
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def handle_api_get(self, parsed):
        conn = get_db()
        cursor = conn.cursor()
        
        if parsed.path == '/api/data':
            cursor.execute("SELECT * FROM teams")
            teams = [dict(t) for t in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM players")
            players = [dict(p) for p in cursor.fetchall()]
            
            player_dict = {p['id']: p for p in players}
            
            for t in teams:
                t['players'] = [p for p in players if p['team_id'] == t['id']]
                if t.get('captain_id') and t['captain_id'] in player_dict:
                    t['captain_name'] = player_dict[t['captain_id']]['name']
                elif not t.get('captain_name'):
                    t['captain_name'] = ''
                
            cursor.execute("SELECT * FROM ties ORDER BY match_num ASC")
            ties = [dict(r) for r in cursor.fetchall()]
            
            for tie in ties:
                cursor.execute("SELECT * FROM matches WHERE tie_id = ? ORDER BY partai_num ASC", (tie['id'],))
                matches = [dict(m) for m in cursor.fetchall()]
                for m in matches:
                    m['t1_p1'] = player_dict.get(m['t1_p1_id'])
                    m['t1_p2'] = player_dict.get(m['t1_p2_id'])
                    m['t2_p1'] = player_dict.get(m['t2_p1_id'])
                    m['t2_p2'] = player_dict.get(m['t2_p2_id'])
                tie['matches'] = matches
                
            standings = recalculate_standings()
            
            cursor.execute("SELECT * FROM settings")
            settings = {r['key']: r['value'] for r in cursor.fetchall()}
            
            response_data = {
                'settings': settings,
                'teams': teams,
                'players': players,
                'ties': ties,
                'standings': standings
            }
            self.send_json(response_data)
            
        else:
            self.send_error(404, "API Endpoint Not Found")
        conn.close()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            payload = json.loads(body.decode('utf-8'))
        except Exception:
            payload = {}

        conn = get_db()
        cursor = conn.cursor()

        if parsed.path == '/api/teams':
            action = payload.get('action')
            if action == 'create':
                name = payload.get('name', '').strip()
                color = payload.get('color', '#2563EB')
                cursor.execute("INSERT INTO teams (name, color, captain_name) VALUES (?, ?, '')", (name, color))
                conn.commit()
                
                cursor.execute("SELECT id FROM teams ORDER BY id ASC LIMIT 4")
                teams = [r['id'] for r in cursor.fetchall()]
                if len(teams) == 4:
                    t1, t2, t3, t4 = teams[0], teams[1], teams[2], teams[3]
                    cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE match_num = 1", (t1, t2))
                    cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE match_num = 2", (t3, t4))
                    cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE match_num = 3", (t1, t3))
                    cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE match_num = 4", (t2, t4))
                    cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE match_num = 5", (t1, t4))
                    cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE match_num = 6", (t4, t3))
                    conn.commit()
                    
                self.send_json({'success': True, 'id': cursor.lastrowid})
            elif action == 'update':
                team_id = payload.get('id')
                name = payload.get('name', '').strip()
                color = payload.get('color', '#2563EB')
                captain_id = payload.get('captain_id')
                captain_name = payload.get('captain_name', '').strip()
                
                if captain_id:
                    cursor.execute("SELECT name FROM players WHERE id = ?", (captain_id,))
                    row = cursor.fetchone()
                    if row:
                        captain_name = row['name']
                        
                cursor.execute("UPDATE teams SET name = ?, color = ?, captain_id = ?, captain_name = ? WHERE id = ?",
                               (name, color, captain_id, captain_name, team_id))
                conn.commit()
                self.send_json({'success': True})
            elif action == 'set_captain':
                team_id = payload.get('id')
                captain_id = payload.get('captain_id')
                captain_name = ''
                if captain_id:
                    cursor.execute("SELECT name FROM players WHERE id = ?", (captain_id,))
                    row = cursor.fetchone()
                    if row:
                        captain_name = row['name']
                cursor.execute("UPDATE teams SET captain_id = ?, captain_name = ? WHERE id = ?", (captain_id, captain_name, team_id))
                conn.commit()
                self.send_json({'success': True})
            elif action == 'delete':
                team_id = payload.get('id')
                cursor.execute("DELETE FROM teams WHERE id = ?", (team_id,))
                cursor.execute("UPDATE players SET team_id = NULL WHERE team_id = ?", (team_id,))
                conn.commit()
                self.send_json({'success': True})
            else:
                self.send_error(400, "Invalid action")

        elif parsed.path == '/api/players':
            action = payload.get('action')
            if action == 'create':
                name = payload.get('name', '').strip()
                team_id = payload.get('team_id')
                tier = payload.get('tier', 'B')
                power_point = TIER_POINTS.get(tier, 3)
                is_late = payload.get('is_late', 0)
                cursor.execute("INSERT INTO players (name, team_id, tier, power_point, is_late) VALUES (?, ?, ?, ?, ?)",
                               (name, team_id, tier, power_point, is_late))
                conn.commit()
                self.send_json({'success': True, 'id': cursor.lastrowid})
            elif action == 'update':
                player_id = payload.get('id')
                name = payload.get('name', '').strip()
                team_id = payload.get('team_id')
                tier = payload.get('tier', 'B')
                power_point = TIER_POINTS.get(tier, 3)
                is_late = payload.get('is_late', 0)
                cursor.execute("UPDATE players SET name = ?, team_id = ?, tier = ?, power_point = ?, is_late = ? WHERE id = ?",
                               (name, team_id, tier, power_point, is_late, player_id))
                conn.commit()
                self.send_json({'success': True})
            elif action == 'toggle_late':
                player_id = payload.get('id')
                cursor.execute("UPDATE players SET is_late = CASE WHEN is_late = 1 THEN 0 ELSE 1 END WHERE id = ?", (player_id,))
                conn.commit()
                self.send_json({'success': True})
            elif action == 'delete':
                player_id = payload.get('id')
                cursor.execute("DELETE FROM players WHERE id = ?", (player_id,))
                conn.commit()
                self.send_json({'success': True})
            else:
                self.send_error(400, "Invalid action")

        elif parsed.path == '/api/ties/update':
            tie_id = payload.get('tie_id')
            t1_id = payload.get('team1_id')
            t2_id = payload.get('team2_id')
            t1_val = int(t1_id) if t1_id else None
            t2_val = int(t2_id) if t2_id else None
            
            cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE id = ?", (t1_val, t2_val, tie_id))
            conn.commit()
            self.send_json({'success': True})

        elif parsed.path == '/api/lineups/auto':
            tie_id = payload.get('tie_id')
            if tie_id:
                ties_to_gen = [dict(r) for r in cursor.execute("SELECT * FROM ties WHERE id = ?", (tie_id,)).fetchall()]
            else:
                ties_to_gen = [dict(r) for r in cursor.execute("SELECT * FROM ties").fetchall()]

            cursor.execute("SELECT * FROM players")
            all_players = [dict(p) for p in cursor.fetchall()]

            for tie in ties_to_gen:
                t1_id = tie['team1_id']
                t2_id = tie['team2_id']
                if not t1_id or not t2_id:
                    continue

                t1_players = [p for p in all_players if p['team_id'] == t1_id]
                t2_players = [p for p in all_players if p['team_id'] == t2_id]

                t1_pairs = sort_pairings_for_team(t1_players)
                t2_pairs = sort_pairings_for_team(t2_players)

                cursor.execute("SELECT id, partai_num FROM matches WHERE tie_id = ? ORDER BY partai_num ASC", (tie['id'],))
                matches = [dict(m) for m in cursor.fetchall()]

                for idx, m in enumerate(matches):
                    p1 = t1_pairs[idx] if idx < len(t1_pairs) else [None, None]
                    p2 = t2_pairs[idx] if idx < len(t2_pairs) else [None, None]

                    t1_p1_id = p1[0]['id'] if p1[0] else None
                    t1_p2_id = p1[1]['id'] if len(p1) > 1 and p1[1] else None
                    t2_p1_id = p2[0]['id'] if p2[0] else None
                    t2_p2_id = p2[1]['id'] if len(p2) > 1 and p2[1] else None

                    pts1 = sum(p['power_point'] for p in p1 if p)
                    pts2 = sum(p['power_point'] for p in p2 if p)

                    pen1 = 5 if any(p and p.get('is_late', 0) for p in p1) else 0
                    pen2 = 5 if any(p and p.get('is_late', 0) for p in p2) else 0

                    cursor.execute('''
                        UPDATE matches SET
                            t1_p1_id = ?, t1_p2_id = ?, t2_p1_id = ?, t2_p2_id = ?,
                            pair1_points = ?, pair2_points = ?,
                            t1_penalty = ?, t2_penalty = ?
                        WHERE id = ?
                    ''', (t1_p1_id, t1_p2_id, t2_p1_id, t2_p2_id, pts1, pts2, pen1, pen2, m['id']))

            conn.commit()
            self.send_json({'success': True})

        elif parsed.path == '/api/matches/score':
            match_id = payload.get('match_id')
            tie_id_param = payload.get('tie_id')
            team1_id_param = payload.get('team1_id')
            team2_id_param = payload.get('team2_id')

            if tie_id_param and (team1_id_param is not None or team2_id_param is not None):
                t1_val = int(team1_id_param) if team1_id_param else None
                t2_val = int(team2_id_param) if team2_id_param else None
                cursor.execute("UPDATE ties SET team1_id = ?, team2_id = ? WHERE id = ?", (t1_val, t2_val, tie_id_param))
                conn.commit()

            t1_score = int(payload.get('t1_score', 0))
            t2_score = int(payload.get('t2_score', 0))
            
            t1_p1_id = payload.get('t1_p1_id')
            t1_p2_id = payload.get('t1_p2_id')
            t2_p1_id = payload.get('t2_p1_id')
            t2_p2_id = payload.get('t2_p2_id')
            
            cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
            m = cursor.fetchone()
            if m:
                tie_id = m['tie_id']
                cursor.execute("SELECT * FROM ties WHERE id = ?", (tie_id,))
                tie = cursor.fetchone()
                
                cursor.execute("SELECT * FROM players")
                all_players = {p['id']: dict(p) for p in cursor.fetchall()}
                
                p1_id_val = int(t1_p1_id) if t1_p1_id else m['t1_p1_id']
                p2_id_val = int(t1_p2_id) if t1_p2_id else m['t1_p2_id']
                p3_id_val = int(t2_p1_id) if t2_p1_id else m['t2_p1_id']
                p4_id_val = int(t2_p2_id) if t2_p2_id else m['t2_p2_id']
                
                p1_obj = all_players.get(p1_id_val)
                p2_obj = all_players.get(p2_id_val)
                p3_obj = all_players.get(p3_id_val)
                p4_obj = all_players.get(p4_id_val)
                
                pair1_pts = (p1_obj['power_point'] if p1_obj else 0) + (p2_obj['power_point'] if p2_obj else 0)
                pair2_pts = (p3_obj['power_point'] if p3_obj else 0) + (p4_obj['power_point'] if p4_obj else 0)
                
                pen1 = 5 if ((p1_obj and p1_obj.get('is_late')) or (p2_obj and p2_obj.get('is_late'))) else 0
                pen2 = 5 if ((p3_obj and p3_obj.get('is_late')) or (p4_obj and p4_obj.get('is_late'))) else 0
                
                winner_id = None
                if t1_score > t2_score:
                    winner_id = tie['team1_id']
                elif t2_score > t1_score:
                    winner_id = tie['team2_id']
                    
                status = 'Completed' if (t1_score > 0 or t2_score > 0) else 'Scheduled'
                cursor.execute('''
                    UPDATE matches SET
                        t1_p1_id = ?, t1_p2_id = ?, t2_p1_id = ?, t2_p2_id = ?,
                        pair1_points = ?, pair2_points = ?,
                        t1_penalty = ?, t2_penalty = ?,
                        t1_score = ?, t2_score = ?, status = ?, winner_team_id = ?
                    WHERE id = ?
                ''', (
                    p1_id_val, p2_id_val, p3_id_val, p4_id_val,
                    pair1_pts, pair2_pts, pen1, pen2,
                    t1_score, t2_score, status, winner_id, match_id
                ))
                
                cursor.execute("SELECT * FROM matches WHERE tie_id = ?", (tie_id,))
                all_m = [dict(x) for x in cursor.fetchall()]
                t1_wins = sum(1 for x in all_m if x['winner_team_id'] == tie['team1_id'])
                t2_wins = sum(1 for x in all_m if x['winner_team_id'] == tie['team2_id'])
                
                completed_count = sum(1 for x in all_m if x['status'] == 'Completed')
                tie_status = 'Live' if completed_count > 0 else 'Scheduled'
                tie_winner = None
                
                if completed_count == 3 or t1_wins >= 2 or t2_wins >= 2:
                    tie_status = 'Completed'
                    if t1_wins > t2_wins:
                        tie_winner = tie['team1_id']
                    elif t2_wins > t1_wins:
                        tie_winner = tie['team2_id']
                        
                cursor.execute("UPDATE ties SET score1 = ?, score2 = ?, status = ?, winner_team_id = ? WHERE id = ?",
                               (t1_wins, t2_wins, tie_status, tie_winner, tie_id))
                conn.commit()
                
                update_playoff_teams()
                self.send_json({'success': True})
            else:
                self.send_error(404, "Match not found")

        elif parsed.path == '/api/seed':
            cursor.execute("DELETE FROM players")
            cursor.execute("DELETE FROM teams")
            cursor.execute("DELETE FROM matches")
            cursor.execute("DELETE FROM ties")
            
            teams_data = [
                ("Tim Alpha", "#DC2626", ""),
                ("Tim Bravo", "#2563EB", ""),
                ("Tim Charlie", "#16A34A", ""),
                ("Tim Delta", "#D97706", ""),
            ]
            
            team_ids = []
            for name, color, cap in teams_data:
                cursor.execute("INSERT INTO teams (name, color, captain_name) VALUES (?, ?, ?)", (name, color, cap))
                team_ids.append(cursor.lastrowid)
                
            sample_names = [
                ["Andi (A1)", "Budi (A2)", "Cakra (B)", "Deni (B)", "Eko (C1)", "Fajar (C2)"],
                ["Gilang (A1)", "Hery (A2)", "Irfan (B)", "Joko (B)", "Kiki (C1)", "Lukman (C2)"],
                ["Mario (A1)", "Niko (A2)", "Oscar (B)", "Putra (B)", "Qori (C1)", "Rian (C2)"],
                ["Soni (A1)", "Tono (A2)", "Umar (B)", "Vino (B)", "Wawan (C1)", "Yudi (C2)"]
            ]
            tiers = ['A1', 'A2', 'B', 'B', 'C1', 'C2']
            
            for t_idx, t_id in enumerate(team_ids):
                captain_p_id = None
                for p_idx, p_name in enumerate(sample_names[t_idx]):
                    tr = tiers[p_idx]
                    pts = TIER_POINTS[tr]
                    is_late = 1 if (t_idx == 0 and p_idx == 1) else 0
                    cursor.execute("INSERT INTO players (name, team_id, tier, power_point, is_late) VALUES (?, ?, ?, ?, ?)",
                                   (p_name, t_id, tr, pts, is_late))
                    if p_idx == 0:
                        captain_p_id = cursor.lastrowid
                cursor.execute("UPDATE teams SET captain_id = ?, captain_name = ? WHERE id = ?", (captain_p_id, sample_names[t_idx][0], t_id))
                                   
            conn.commit()
            
            t1, t2, t3, t4 = team_ids[0], team_ids[1], team_ids[2], team_ids[3]
            schedule = [
                ("Group Stage", "Match 1", 1, t1, t2),
                ("Group Stage", "Match 2", 2, t3, t4),
                ("Group Stage", "Match 3", 3, t1, t3),
                ("Group Stage", "Match 4", 4, t2, t4),
                ("Group Stage", "Match 5", 5, t1, t4),
                ("Group Stage", "Match 6", 6, t4, t3),
                ("Playoff", "Final Juara 1", 7, None, None),
                ("Playoff", "Perebutan Juara 3", 8, None, None),
            ]
            for stage, round_name, match_num, team1_id, team2_id in schedule:
                cursor.execute(
                    "INSERT INTO ties (stage, round_name, match_num, team1_id, team2_id) VALUES (?, ?, ?, ?, ?)",
                    (stage, round_name, match_num, team1_id, team2_id)
                )
                tie_id = cursor.lastrowid
                for p_num in (1, 2, 3):
                    cursor.execute(
                        "INSERT INTO matches (tie_id, partai_num) VALUES (?, ?)",
                        (tie_id, p_num)
                    )
            conn.commit()
            
            cursor.execute("SELECT id FROM ties")
            for row in cursor.fetchall():
                tie_id = row['id']
                cursor.execute("SELECT * FROM ties WHERE id = ?", (tie_id,))
                tie = dict(cursor.fetchone())
                t1_id = tie['team1_id']
                t2_id = tie['team2_id']
                if t1_id and t2_id:
                    cursor.execute("SELECT * FROM players WHERE team_id = ?", (t1_id,))
                    t1_players = [dict(p) for p in cursor.fetchall()]
                    cursor.execute("SELECT * FROM players WHERE team_id = ?", (t2_id,))
                    t2_players = [dict(p) for p in cursor.fetchall()]
                    
                    t1_pairs = sort_pairings_for_team(t1_players)
                    t2_pairs = sort_pairings_for_team(t2_players)
                    
                    cursor.execute("SELECT id FROM matches WHERE tie_id = ? ORDER BY partai_num ASC", (tie_id,))
                    matches = [dict(m) for m in cursor.fetchall()]
                    for idx, m in enumerate(matches):
                        p1 = t1_pairs[idx] if idx < len(t1_pairs) else [None, None]
                        p2 = t2_pairs[idx] if idx < len(t2_pairs) else [None, None]
                        
                        t1_p1_id = p1[0]['id'] if p1[0] else None
                        t1_p2_id = p1[1]['id'] if len(p1) > 1 and p1[1] else None
                        t2_p1_id = p2[0]['id'] if p2[0] else None
                        t2_p2_id = p2[1]['id'] if len(p2) > 1 and p2[1] else None
                        
                        pts1 = sum(p['power_point'] for p in p1 if p)
                        pts2 = sum(p['power_point'] for p in p2 if p)
                        pen1 = 5 if any(p and p.get('is_late', 0) for p in p1) else 0
                        pen2 = 5 if any(p and p.get('is_late', 0) for p in p2) else 0
                        
                        cursor.execute('''
                            UPDATE matches SET
                                t1_p1_id = ?, t1_p2_id = ?, t2_p1_id = ?, t2_p2_id = ?,
                                pair1_points = ?, pair2_points = ?,
                                t1_penalty = ?, t2_penalty = ?
                            WHERE id = ?
                        ''', (t1_p1_id, t1_p2_id, t2_p1_id, t2_p2_id, pts1, pts2, pen1, pen2, m['id']))
            conn.commit()
            self.send_json({'success': True})

        elif parsed.path == '/api/reset':
            cursor.execute("DELETE FROM players")
            cursor.execute("DELETE FROM teams")
            cursor.execute("DELETE FROM matches")
            cursor.execute("DELETE FROM ties")
            conn.commit()
            ensure_default_ties()
            self.send_json({'success': True})

        else:
            self.send_error(404, "API Endpoint Not Found")
        conn.close()

    def send_json(self, data):
        content = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

if __name__ == '__main__':
    init_db()
    print(f"PBSC Badminton Server running on http://127.0.0.1:{PORT}")
    with socketserver.TCPServer(("", PORT), PBSCHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
