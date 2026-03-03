games_data = {}

def add_player(room, player_id, player_name):
    if room not in games_data:
        games_data[room] = {'players_count': 1, 'players_ids': [player_id], 'players_names': {player_id: player_name}, 'secrets': {}, 'turn': None, 'current_guess': None}
        return False
    else:
        if games_data[room]['players_count'] == 1:
            games_data[room]['players_count'] += 1
            games_data[room]['players_ids'].append(player_id)
            games_data[room]['players_names'][player_id] = player_name
            return True
    return False

def set_secret(room, player_id, secret):
    games_data[room]['secrets'][player_id] = secret
    if len(games_data[room]['secrets']) == 2:
        first_player = games_data[room]['players_ids'][0]
        games_data[room]['turn'] = first_player
        return first_player, games_data[room]['players_names'][first_player]
    return None, None

def save_guess(room, guess):
    games_data[room]['current_guess'] = guess

def get_guesser_name(room, player_id):
    return games_data[room]['players_names'][player_id]

def check_cheat(room, answerer_id, answer):
    actual_secret = games_data[room]['secrets'][answerer_id]
    current_guess = games_data[room]['current_guess']
    if answer == 'أعلى' and actual_secret <= current_guess: return True
    if answer == 'أقل' and actual_secret >= current_guess: return True
    if answer == 'أيوة ده رقمي' and actual_secret != current_guess: return True
    return False

def process_answer(room):
    players = games_data[room]['players_ids']
    current_turn = games_data[room]['turn']
    previous_name = games_data[room]['players_names'][current_turn]
    guess = games_data[room]['current_guess']
    
    next_turn = players[1] if current_turn == players[0] else players[0]
    next_turn_name = games_data[room]['players_names'][next_turn]
    games_data[room]['turn'] = next_turn
    
    return next_turn, next_turn_name, previous_name, guess

def get_winner_info(room):
    winner_id = games_data[room]['turn']
    return winner_id, games_data[room]['players_names'][winner_id]