import pygame
import socket
import threading
import random
import time
import pickle
import struct
import select
import sys
import traceback
import json  # <--- 新增
import os    # <--- 新增

# --- 全局配置加载 ---
def load_config():
    default_conf = {
        "game_settings": {
            "max_hp": 10, "max_trumps_hand_size": 20, "max_active_trumps_on_table": 10, "target_score": 21,
            "initial_trumps_count": 4, "round_reward_trumps_count": 1,
            "hit_draw_trump_probability": 0.35, "number_card_draw_probability": 0.166,
            "result_screen_duration": 4.0, "deck_range_start": 1, "deck_range_end": 11
        },
        "trump_weights": {} 
    }
    try:
        # === 【最终兼容版：自动识别是脚本还是exe】 ===
        if getattr(sys, 'frozen', False):
            # 如果是打包后的 exe，使用 exe 所在目录
            base_dir = os.path.dirname(sys.executable)
        else:
            # 如果是 python 脚本，使用 脚本 所在目录
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        config_path = os.path.join(base_dir, "config.json")
        
        # print(f"Looking for config at: {config_path}") # 调试用，打包后看不到控制台可以注释掉

        if os.path.exists(config_path):
            with open(config_path, "r", encoding='utf-8') as f:
                data = json.load(f)
                for k, v in data.items():
                    if k in default_conf:
                        default_conf[k].update(v)
        else:
            # 这里可以不做处理，保持沉默，使用默认值
            pass
            
    except Exception:
        pass # 出错保持沉默，使用默认值
    return default_conf
GAME_CONFIG = load_config()
SETTINGS = GAME_CONFIG["game_settings"]
WEIGHTS = GAME_CONFIG["trump_weights"]

# --- 全局常量映射 (使用配置文件的值) ---
MAX_TRUMPS = SETTINGS["max_trumps_hand_size"]
MAX_HP = SETTINGS["max_hp"] # <--- 这里直接调用配置
MAX_TABLE_SLOTS = SETTINGS["max_active_trumps_on_table"]

# --- 全局配置 ---
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 768
#MAX_TRUMPS = 10
DEFAULT_PORT = 6666
DEFAULT_HOST_IP = "10.144.51.1"
#索引：修改血量
#MAX_HP = 10  # <--- 【新增/修改这里】 改成 5 就是 5个格子，改成 10 就是 10个格子
# --- 配色方案 ---
C_BG = (8, 8, 10)               
C_TABLE = (25, 25, 30)          
C_CARD_BG = (230, 225, 210)     
C_CARD_BACK = (90, 20, 20)      
C_TEXT_MAIN = (240, 240, 240)   
C_TEXT_DIM = (120, 120, 120)    
C_HIGHLIGHT = (255, 200, 50)    
C_FINGER_ALIVE = (180, 40, 40)  
C_FINGER_DEAD = (20, 5, 5)      
C_FINGER_X = (255, 0, 0)  
C_BTN_IDLE = (60, 60, 70)
C_BTN_HOVER = (80, 80, 90)
C_BTN_DISABLED = (40, 40, 40)
C_ERROR = (255, 80, 80)
C_GREEN = (50, 200, 50)

# --- 游戏逻辑 ---
class GameState:
    def __init__(self):
        self.deck = []
        self.p1_hand = []
        self.p2_hand = []
        self.p1_trumps = []
        self.p2_trumps = []
        self.active_trumps = [] 
        
        # === 【新增】将配置同步给客机 ===
        self.max_hp_limit = MAX_HP 
        # ============================

        self.p1_fingers = MAX_HP
        self.p2_fingers = MAX_HP
        self.target_score = 21
        
        self.round_starter = 1
        self.turn = 1
        self.phase = "ACTION"
        self.round_id = 0 
        
        self.round_winner = 0 
        self.round_damage = 0
        self.result_timer = 0
        
        self.p1_stop = False
        self.p2_stop = False
        self.p1_req_rematch = False
        self.p2_req_rematch = False
        
        self.last_action_time = {1: 0.0, 2: 0.0}
        
        self.give_trump(1, SETTINGS["initial_trumps_count"])       #索引：初始王牌数目（x+1）
        self.give_trump(2, SETTINGS["initial_trumps_count"])
        self.reset_round(init=True)

         # === 【修改点 1】给列表加上 self. 前缀 ===
        self.INSTANT_TYPES = [
            "DRAW_SPEC", "DRAW_SPEC_PLUS",  
            "RETURN", "REMOVE",             
            "DESTROY", "DESTROY_SINGLE", "DESTROY_BLOCK", 
            "PERFECT", "LOVE",              
            "CHANGE", "TRUMP_EXCHANGE", "TRUMP_EXCHANGE_PLUS", 
            "HAPPINESS", "CURSE",           
            "MAGIC_DRAW", "ULTIMATE_DRAW", "HARVEST", 
            "OBLIVION", "ESCAPE"            
        ]

    # === 【修改点 2】注意！这个 def 必须向左靠，和上面的 def __init__ 垂直对齐 ===
    # (也就是要向左删除 4 个空格)
    def cleanup_player_instants(self, pid):
        """清理指定玩家场上的一次性废卡"""
        self.active_trumps = [
            t for t in self.active_trumps 
            # 注意这里调用时也要加 self.INSTANT_TYPES
            if t['owner'] != pid or t['type'] not in self.INSTANT_TYPES
        ]
        

    def generate_deck(self):
        # 将 range(1, 12) 修改为配置
        start = SETTINGS["deck_range_start"]
        end = SETTINGS["deck_range_end"] + 1 # range是左闭右开，所以+1
        self.deck = [i for i in range(start, end)]
        random.shuffle(self.deck)

    def get_trump_card(self):
        # === 1. 数字卡池 (保持 2-7) ===
        number_pool = [
            ("Two", "DRAW_SPEC", 2), ("Three", "DRAW_SPEC", 3),
            ("Four", "DRAW_SPEC", 4), ("Five", "DRAW_SPEC", 5),
            ("Six", "DRAW_SPEC", 6), ("Seven", "DRAW_SPEC", 7)
        ]

        # === 2. 功能卡池配置 (卡牌数据, 权重) ===
        # 权重(Weight)越高，越容易抽到。
        # 比如 Trump+ 设为 30，Add 1 设为 15，那么抽到 Trump+ 的概率就是 Add 1 的两倍
        # 格式: ( (名称, 类型, 数值), WEIGHTS.get("名称", 默认值) )
        special_config = [
            (("Add 1", "ADD", 1), WEIGHTS.get("Add 1", 14)),
            (("Add 2", "ADD", 2), WEIGHTS.get("Add 2", 8)),
            (("Shield", "SHIELD", 1), WEIGHTS.get("Shield", 10)),
            (("Shield+", "SHIELD", 2), WEIGHTS.get("Shield+", 5)),
            
            (("Destroy", "DESTROY_SINGLE", 0), WEIGHTS.get("Destroy", 0)),
            (("Destroy+", "DESTROY", 0), WEIGHTS.get("Destroy+", 7)),
            (("Destroy++", "DESTROY_BLOCK", 0), WEIGHTS.get("Destroy++", 0)),
            
            (("Return", "RETURN", 0), WEIGHTS.get("Return", 9)),
            (("ADD2+", "RETURN_PLUS", 2), WEIGHTS.get("Return+", 0)), # 注意json里的名字
            
            (("Remove", "REMOVE", 0), WEIGHTS.get("Remove", 9)),
            
            (("Perfect", "PERFECT", 0), WEIGHTS.get("Perfect", 8)),
            (("Perfect+", "PERFECT_PLUS", 5), WEIGHTS.get("Perfect+", 0)),
            
            (("Go 17", "TARGET", 17), WEIGHTS.get("Go 17", 7)),
            (("Go 24", "TARGET", 24), WEIGHTS.get("Go 24", 7)),
            (("Go 27", "TARGET", 27), WEIGHTS.get("Go 27", 7)),
            
            (("Change", "CHANGE", 0), WEIGHTS.get("Change", 8)),
            (("Trump+", "TRUMP_EXCHANGE", 0), WEIGHTS.get("Trump+", 15)),
            (("Trump++", "TRUMP_EXCHANGE_PLUS", 0), WEIGHTS.get("Trump++", 0)),
            
            (("S-Attack", "SHIELD_ATTACK", 3), WEIGHTS.get("S-Attack", 0)),
            (("S-Attack+", "SHIELD_ATTACK_PLUS", 5), WEIGHTS.get("S-Attack+", 0)),
            
            (("Waste", "FORCE_CONSUME", 2), WEIGHTS.get("Waste", 0)),
            (("Waste+", "FORCE_CONSUME_PLUS", 3), WEIGHTS.get("Waste+", 0)),
            
            (("Desire", "DESIRE", 0), WEIGHTS.get("Desire", 0)),
            (("Desire+", "DESIRE_PLUS", 0), WEIGHTS.get("Desire+", 0)),
            
            (("Love", "LOVE", 0), WEIGHTS.get("Love", 0)),
            (("Gamble", "GAMBLE", 100), WEIGHTS.get("Gamble", 0)),
            (("D-Destroy", "DEATH_DESTROY", 10), WEIGHTS.get("D-Destroy", 0)),
            (("Add 21", "ADD_21", 21), WEIGHTS.get("Add 21", 0)),
            (("Happiness", "HAPPINESS", 0), WEIGHTS.get("Happiness", 0)),
            (("Curse", "CURSE", 0), WEIGHTS.get("Curse", 0)),
            (("M-Draw", "MAGIC_DRAW", 1), WEIGHTS.get("M-Draw", 0)),
            (("Silence", "SILENCE", 0), WEIGHTS.get("Silence", 0)),
            (("Oblivion", "OBLIVION", 0), WEIGHTS.get("Oblivion", 0)),
            (("Harvest", "HARVEST", 0), WEIGHTS.get("Harvest", 0)),
            (("Escape", "ESCAPE", 0), WEIGHTS.get("Escape", 0)),
            (("U-Draw", "ULTIMATE_DRAW", 0), WEIGHTS.get("U-Draw", 0)),
            
            (("Two+", "DRAW_SPEC_PLUS", 2), WEIGHTS.get("Two+", 0)),
            (("Three+", "DRAW_SPEC_PLUS", 3), WEIGHTS.get("Three+", 0)),
            (("Four+", "DRAW_SPEC_PLUS", 4), WEIGHTS.get("Four+", 0)),
            (("Five+", "DRAW_SPEC_PLUS", 5), WEIGHTS.get("Five+", 0)),
            (("Six+", "DRAW_SPEC_PLUS", 6), WEIGHTS.get("Six+", 0)),
            (("Seven+", "DRAW_SPEC_PLUS", 7), WEIGHTS.get("Seven+", 0)),
        ]

        # === 3. 抽卡逻辑 ===
        threshold = SETTINGS["number_card_draw_probability"]

        # 第一层判定：1/6 概率抽数字卡，5/6 概率抽功能卡 (保持原味平衡)
        if random.random() < threshold:
            return random.choice(number_pool)
        else:
            # 第二层判定：根据权重抽取功能卡
            # 解包数据和权重
            cards = [item[0] for item in special_config]
            weights = [item[1] for item in special_config]
            
            # random.choices 返回的是列表，所以要取 [0]
            # k=1 表示抽 1 张
            # 防止权重全为0导致报错
            if sum(weights) == 0: return random.choice(number_pool)
            return random.choices(population=cards, weights=weights, k=1)[0]

    def give_trump(self, pid, count=1):
        target = self.p1_trumps if pid == 1 else self.p2_trumps
        for _ in range(count):
            if len(target) < MAX_TRUMPS:
                target.append(self.get_trump_card())

    def full_reset(self):
        self.p1_fingers = MAX_HP
        self.p2_fingers = MAX_HP
        self.p1_trumps = []
        self.p2_trumps = []
        self.active_trumps = []
        self.p1_req_rematch = False
        self.p2_req_rematch = False
        self.give_trump(1, 3)
        self.give_trump(2, 3)
        self.reset_round(init=True)

    def reset_round(self, init=False):
        self.is_escape_end = False  # <--- 【新增】初始化逃脱结束标记
        self.generate_deck()
        self.p1_hand = []
        self.p2_hand = []
        self.p1_stop = False
        self.p2_stop = False
        self.active_trumps = []
        self.target_score = 21
        self.phase = "ACTION"
        self.round_winner = 0
        self.round_damage = 0
        self.round_id += 1
        
        self.target_score = SETTINGS["target_score"] # 使用配置的目标分

        if init: self.round_starter = 1
        else: self.round_starter = 3 - self.round_starter
        self.turn = self.round_starter
        #回合奖励
        reward = SETTINGS["round_reward_trumps_count"]
        self.give_trump(1, reward)
        self.give_trump(2, reward)
        self.draw_card(1, check_trump=False); self.draw_card(1, check_trump=False)
        self.draw_card(2, check_trump=False); self.draw_card(2, check_trump=False)

    def get_current_bet(self):
        bet = 1
        for t in self.active_trumps:
            if t['type'] == 'ADD': bet += t['val']
        return bet

    def calculate_potential_damage(self, target_pid):
        damage = 1 
        for t in self.active_trumps:
            if t['type'] == 'ADD' and t['owner'] != target_pid:
                damage += t['val']
            elif t['type'] == 'SHIELD' and t['owner'] == target_pid:
                damage -= t['val']

            # === 【新增】欲望系列逻辑 ===
            # 如果这张卡是对手放的 (owner != target_pid)，且是欲望卡
            elif t['type'] == 'DESIRE' and t['owner'] != target_pid:
                # 获取受害者(target_pid)当前持有的王牌列表
                victim_trumps = self.p1_trumps if target_pid == 1 else self.p2_trumps
                # 增加持有数量的一半 (向下取整)
                damage += len(victim_trumps) // 2
                
            elif t['type'] == 'DESIRE_PLUS' and t['owner'] != target_pid:
                # 获取受害者(target_pid)当前持有的王牌列表
                victim_trumps = self.p1_trumps if target_pid == 1 else self.p2_trumps
                # 增加持有数量的全部
                damage += len(victim_trumps)
            # ==========================

            # === 【新增】增加二+ & 完美抽牌+ 的被动增伤 ===
            # 这两张卡只要在场，不论是谁放的（或者是持有者放的），都会增加“对手”的伤害？
            # 根据描述：“当这张牌在牌桌上时，对手的赌注会增加X”
            # 这里的“对手”指的是【卡牌持有者的对手】
            
            elif t['type'] in ['RETURN_PLUS', 'PERFECT_PLUS'] and t['owner'] != target_pid:
                damage += t['val'] # 增加 2 或 5 点伤害

        # === 【新增】生死一搏伤害加成 ===
        # 检查场上是否有 GAMBLE 卡 (不管是谁放的)
        for t in self.active_trumps:
            if t['type'] == 'GAMBLE':
                damage += 100
        # ==============================

        # === 【新增】死亡破坏被动增伤 ===
        for t in self.active_trumps:
            if t['type'] == 'DEATH_DESTROY' and t['owner'] != target_pid:
                damage += t['val'] # 增加 10 点伤害
        # ==============================

        # === 【新增】增加二十一 (ADD_21) ===
        for t in self.active_trumps:
            # 如果这张卡是对手放的 (owner != target_pid)，那么对手是攻击者
            if t['type'] == 'ADD_21' and t['owner'] != target_pid:
                attacker_pid = t['owner']
                # 获取攻击者的手牌
                attacker_hand = self.p1_hand if attacker_pid == 1 else self.p2_hand
                
                # 检查攻击者的点数是否严格等于 21
                # (注意：这里严格检查 21，而不是 target_score，符合卡牌名字)
                if sum(attacker_hand) == 21:
                    damage += t['val'] # 增加 21 点伤害
        # ================================

        # === 【新增】魔抽被动 (MAGIC_DRAW) ===
            # 如果这张卡是受害者(target_pid)自己放的，他受到的伤害要增加
            elif t['type'] == 'MAGIC_DRAW' and t['owner'] == target_pid:
                damage += t['val'] # 伤害 +1
            # ===================================

            

        return max(0, damage)

    def draw_card(self, pid,check_trump=True):
        if not self.deck: return
        card = self.deck.pop()
        if pid == 1: self.p1_hand.append(card)
        else: self.p2_hand.append(card)
        # 2. 只有当 check_trump 为 True 时，才进行概率判定
        if check_trump and random.random() < SETTINGS["hit_draw_trump_probability"]:  #索引：修改HIT王牌概率
            self.give_trump(pid, 1)

    def check_bust(self, pid):
        hand = self.p1_hand if pid == 1 else self.p2_hand
        return sum(hand) > self.target_score

    def resolve_round(self):
        # === 【新增】强迫消耗结算逻辑 ===
        # 遍历场上的牌，寻找未被拆除的陷阱
        for t in self.active_trumps:
            if t['type'] in ["FORCE_CONSUME", "FORCE_CONSUME_PLUS"]:
                # 确定受害者（陷阱所有者的对手）
                victim_pid = 3 - t['owner']
                victim_trumps = self.p1_trumps if victim_pid == 1 else self.p2_trumps
                
                if t['type'] == "FORCE_CONSUME":
                    # 失去一半 (保留一半)
                    # 比如 5 张保留 2 张，丢弃 3 张
                    keep_count = len(victim_trumps) // 2
                    # 切片保留前 keep_count 张
                    if victim_pid == 1: self.p1_trumps = self.p1_trumps[:keep_count]
                    else: self.p2_trumps = self.p2_trumps[:keep_count]
                    
                elif t['type'] == "FORCE_CONSUME_PLUS":
                    # 失去所有
                    if victim_pid == 1: self.p1_trumps = []
                    else: self.p2_trumps = []
        # ======================================

        s1 = sum(self.p1_hand)
        s2 = sum(self.p2_hand)
        b1 = s1 > self.target_score
        b2 = s2 > self.target_score
        
        winner = 0 
        if b1 and b2:
            if s1 < s2: winner = 1
            elif s2 < s1: winner = 2
            else: winner = 0
        elif b1: winner = 2
        elif b2: winner = 1
        else:
            d1 = abs(self.target_score - s1)
            d2 = abs(self.target_score - s2)
            if d1 < d2: winner = 1
            elif d2 < d1: winner = 2
            else: winner = 0
        
        # === 【新增】逃脱机制 (ESCAPE) ===
        # 检查场上是否有 ESCAPE 卡
        has_escape = False
        for t in self.active_trumps:
            if t['type'] == 'ESCAPE':
                has_escape = True
                break
        
        if has_escape:
            winner = 0 # 强制平局
            self.is_escape_end = True # <--- 【新增】标记为强制结束游戏
            # 这样下面的 if winner != 0: 就不会执行，没人会扣血
        # ===============================
            
        damage = 0
        loser = 0
        
        if winner != 0:
            loser = 3 - winner
            damage = self.calculate_potential_damage(loser)
            if loser == 1: self.p1_fingers -= damage
            else: self.p2_fingers -= damage
        
        self.round_winner = winner
        self.round_damage = damage
        self.phase = "RESULT"
        self.result_timer = time.time() + SETTINGS["result_screen_duration"]

    # === 【新增】弃牌逻辑：只删除，不触发效果 ===
    def discard_trump(self, pid, idx):
        trumps = self.p1_trumps if pid == 1 else self.p2_trumps
        # 索引安全检查，防止崩溃
        if 0 <= idx < len(trumps):
            trumps.pop(idx)
            return True
        return False
    
    def use_trump(self, pid, idx):
        trumps = self.p1_trumps if pid == 1 else self.p2_trumps
        if idx >= len(trumps): return None
        
         # === 【新增逻辑 A】检查并更新对手的“强迫消耗”陷阱 ===
        # 只要我用了一张牌，对手场上的“Waste”卡计数器就要 +1
        opp_pid = 3 - pid
        traps_to_remove = []
        
        for t in self.active_trumps:
            # 检查属于对手的陷阱卡
            if t['owner'] == opp_pid and t['type'] in ["FORCE_CONSUME", "FORCE_CONSUME_PLUS"]:
                # 初始化计数器（如果是刚放上去的可能没有这个键）
                if 'counter' not in t: t['counter'] = 0
                
                t['counter'] += 1
                
                # 如果计数器达到阈值 (val即为阈值 2 或 3)
                if t['counter'] >= t['val']:
                    traps_to_remove.append(t)
        
        # 移除被拆除的陷阱
        for t in traps_to_remove:
            self.active_trumps.remove(t)
        # ==================================================

        card = trumps.pop(idx)
        name, ctype, val = card
        
        new_trump = {"owner": pid, "type": ctype, "val": val, "name": name}

        # === 【新增逻辑 B】如果是强迫消耗卡，初始化计数器 ===
        if ctype in ["FORCE_CONSUME", "FORCE_CONSUME_PLUS"]:
            new_trump['counter'] = 0 # 初始计数为 0
        # ==================================================

        
        
        # === 【修正点 2】挑战牌 (TARGET) 取代旧牌逻辑 ===
        if ctype == "TARGET":
            # 先移除场上所有已有的 TARGET 牌
            self.active_trumps = [t for t in self.active_trumps if t['type'] != 'TARGET']
            self.target_score = val # 设置新目标分
        
        self.active_trumps.append(new_trump)
        
        # === 【修正点 1】增加一/二 (ADD) 需要抽一张王牌 ===
        # 注意：S-Attack 也会生成 ADD 类型的效果，但它是通过 SHIELD_ATTACK 转换的，
        # 不会进入这个 if，所以不会导致 S-Attack 误抽牌，符合逻辑。
        if ctype == "ADD":
            self.give_trump(pid, 1)
        
        elif ctype == "DESTROY_SINGLE":
            found_idx = -1
            # 倒序遍历（从最新的牌往回找）
            for i in range(len(self.active_trumps) - 1, -1, -1):
                # 找到一张不属于自己的牌（排除掉自己刚刚打出的这张Destroy卡）
                if self.active_trumps[i]['owner'] != pid:
                    found_idx = i
                    break
            
            if found_idx != -1:
                self.active_trumps.pop(found_idx)
                
                # 【重要】如果炸掉的是 TARGET 卡，必须重算目标分
                self.target_score = 21 # 先重置为默认
                for t in self.active_trumps:
                    if t['type'] == 'TARGET':
                        self.target_score = t['val']
        elif ctype == "DESTROY": 
            self.active_trumps = [t for t in self.active_trumps if t['owner'] == pid]
    
            # 【必须添加】重算目标分逻辑
            self.target_score = 21 # 先重置
            for t in self.active_trumps:
                if t['type'] == 'TARGET':
                    self.target_score = t['val']
        # === 【新增】破坏++ (全体清除 + 封锁光环) ===
        elif ctype == "DESTROY_BLOCK":
            # 1. 清场逻辑：只保留属于自己的牌
            # (这张 DESTROY_BLOCK 刚刚被添加进 active_trumps，属于自己，所以会被保留)
            self.active_trumps = [t for t in self.active_trumps if t['owner'] == pid]
            
            # 2. 重算目标分 (因为对手的 TARGET 卡肯定被清除了)
            self.target_score = 21 
            for t in self.active_trumps:
                if t['type'] == 'TARGET':
                    self.target_score = t['val']
            
            # 注意：封锁逻辑不需要写在这里，只要这张卡保留在场上，
            # 第二步写的 server_worker 拦截代码就会自动生效。       
        elif ctype == "DRAW_SPEC":
            opp_pid = 3 - pid
            is_locked = False
            for t in self.active_trumps:
                # 【修改点】增加 SILENCE 判断
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid:
                    is_locked = True
                    break
            
            if not is_locked: # 只有没被锁才能抽
                if val in self.deck:
                    self.deck.remove(val)
                    (self.p1_hand if pid==1 else self.p2_hand).append(val)
         # === 【新增】数字牌+ (DRAW_SPEC_PLUS) ===
        elif ctype == "DRAW_SPEC_PLUS":
            # 1. 必发效果：抽一张王牌 (回费)
            self.give_trump(pid, 1)
            
            # 2. 条件效果：抽指定数字
            # 必须检查是否被封锁 (Gamble/Silence)
            opp_pid = 3 - pid
            is_draw_locked = False
            for t in self.active_trumps:
                # 检查对手的封锁卡 (根据你之前的定义，Silence也算封锁抽牌)
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid:
                    is_draw_locked = True
                    break
            
            # 只有没被锁，且牌堆里有这张牌，才抽
            if not is_draw_locked:
                if val in self.deck:
                    self.deck.remove(val)
                    (self.p1_hand if pid==1 else self.p2_hand).append(val)
                # 如果不在牌堆里，什么都不发生 (符合需求)
        elif ctype == "RETURN":
            h = self.p1_hand if pid==1 else self.p2_hand
            if len(h)>1: 
                c = h.pop()
                self.deck.insert(random.randint(0, len(self.deck)), c)
        elif ctype == "REMOVE":
            opp = self.p2_hand if pid==1 else self.p1_hand
            if len(opp)>1: 
                c = opp.pop()
                self.deck.insert(random.randint(0, len(self.deck)), c)
        elif ctype == "PERFECT":
                # 【新增】检查封锁
            opp_pid = 3 - pid
            is_locked = False
            for t in self.active_trumps:
                # 【修改点】增加 SILENCE 判断
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid:
                    is_locked = True
                    break
            
            if not is_locked: # 只有没被锁才能执行后续逻辑
                h = self.p1_hand if pid==1 else self.p2_hand
                cur = sum(h)
                safe_cards = [c for c in self.deck if cur + c <= self.target_score]
                best = None
                if safe_cards:
                    best = max(safe_cards)
                else:
                    if self.deck: best = min(self.deck)
                if best: 
                    self.deck.remove(best)
                    h.append(best)
        elif ctype == "CHANGE":
            # 逻辑保护：只有当双方手里都有牌时才能交换
            if len(self.p1_hand) > 1 and len(self.p2_hand) > 1:
                # 1. 弹出双方最后一张牌
                c1 = self.p1_hand.pop()
                c2 = self.p2_hand.pop()
                # 2. 交叉插入回对方的手牌末尾
                self.p1_hand.append(c2)
                self.p2_hand.append(c1)
        elif ctype == "TRUMP_EXCHANGE":
            # 获取当前玩家剩余的王牌列表（Trump+ 已消耗）
            current_trumps = self.p1_trumps if pid == 1 else self.p2_trumps
            
            # --- 稳定视觉版逻辑 ---
            # 只有当剩余牌数 >= 2 时才发动效果（保持硬核设定，不够则白给）
            if len(current_trumps) >= 2:
                # 1. 选出要“删除”的2个位置
                indices_to_replace = random.sample(range(len(current_trumps)), 2)
                
                # 2. 预先抽取 3 张新卡
                new_cards = [self.get_trump_card() for _ in range(3)]
                
                # 3. 【视觉稳定核心】原地替换前两张
                # 我们不 pop 删除，而是直接用新卡覆盖旧卡
                # 这样其他没被选中的牌，suoyin（减少对ctrl+F干扰）完全不会变，视觉上就没动！
                current_trumps[indices_to_replace[0]] = new_cards[0]
                current_trumps[indices_to_replace[1]] = new_cards[1]
                
                # 4. 追加第3张新卡
                # 因为是 删2加3，前两张抵消了，只剩第3张需要占新坑
                if len(current_trumps) < MAX_TRUMPS:
                    current_trumps.append(new_cards[2])
        elif ctype == "SHIELD_ATTACK":
            # 1. 找出该玩家所有的护盾牌
            my_shields = [t for t in self.active_trumps if t['owner'] == pid and t['type'] == 'SHIELD']
            
            # 2. 检查数量是否 >= 3
            if len(my_shields) >= 3:
                # === 优化逻辑开始 ===
                # 对护盾进行排序：按数值(val)从小到大排
                # 这样会先排 'Shield'(1)，后排 'Shield+'(2)
                my_shields.sort(key=lambda x: x['val'])
                
                # 3. 撤销前 3 张（也就是数值最小的 3 张）
                cards_to_remove = my_shields[:3]
                
                for s in cards_to_remove:
                    self.active_trumps.remove(s)
                # === 优化逻辑结束 ===

                # 4. 激活伤害效果
                new_trump['type'] = 'ADD'
                # val 默认为 3
            else:
                pass
        elif ctype == "SHIELD_ATTACK_PLUS":
            my_shields = [t for t in self.active_trumps if t['owner'] == pid and t['type'] == 'SHIELD']
            
            if len(my_shields) >= 2:
                # === 优化逻辑 ===
                # 同样从小到大排序，优先消耗垃圾盾
                my_shields.sort(key=lambda x: x['val'])
                
                # 撤销前 2 张
                cards_to_remove = my_shields[:2]
                
                for s in cards_to_remove:
                    self.active_trumps.remove(s)
                # === 结束 ===
                
                # 激活伤害效果 (val 默认为 5)
                new_trump['type'] = 'ADD'
            else:
                pass

        # === 【新增】爱你的敌人 (Love) ===
        elif ctype == "LOVE":
            # 这里检查的是“我”有没有放封锁卡
            my_lock = False
            for t in self.active_trumps:
                # 【修改点】增加 SILENCE 判断
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == pid:
                    my_lock = True
                    break

            if not my_lock:
                # 1. 确定对手是谁
                opp_pid = 3 - pid
                opp_hand = self.p1_hand if opp_pid == 1 else self.p2_hand
                if sum(opp_hand) <= self.target_score:
                    # 2. 计算对手当前的牌面和
                    cur = sum(opp_hand)
                    
                    # 3. 寻找“最佳卡” (逻辑同 PERFECT)
                    # 找出所有不会导致对手爆牌的卡
                    safe_cards = [c for c in self.deck if cur + c <= self.target_score]
                    best = None
                    
                    if safe_cards:
                        # 如果有安全卡，选最大的那张 (帮他凑近 21)
                        best = max(safe_cards)
                    else:
                        # 如果没有安全卡 (必爆)，选最小的那张 (帮他死得轻一点)
                        if self.deck: best = min(self.deck)
                    
                    # 4. 强制发牌
                    if best: 
                        self.deck.remove(best)
                        opp_hand.append(best)
        # === 【新增】死亡破坏 (DEATH_DESTROY) ===
        elif ctype == "DEATH_DESTROY":
            # 1. 【代价】放弃一半王牌
            # 获取当前剩余的王牌列表 (注意：这张 D-Destroy 已经被移出列表了)
            current_trumps = self.p1_trumps if pid == 1 else self.p2_trumps
            count = len(current_trumps)
            if count > 0:
                # 计算要丢弃的数量 (向下取整，比如 5张丢2张留3张)
                discard_num = count // 2
                # 执行丢弃 (保留列表的后半部分)
                # 这里的切片操作会直接修改列表引用，需要重新赋值回 self 对象
                if pid == 1: 
                    self.p1_trumps = self.p1_trumps[discard_num:]
                else: 
                    self.p2_trumps = self.p2_trumps[discard_num:]

            # 2. 【效果】抽出数字最有利的牌 (逻辑同 PERFECT)
            # 必须检查是否有“生死一搏(GAMBLE)”封锁
            opp_pid = 3 - pid
            is_locked = any(t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid for t in self.active_trumps)
            
            if not is_locked:
                h = self.p1_hand if pid==1 else self.p2_hand
                cur = sum(h)
                safe_cards = [c for c in self.deck if cur + c <= self.target_score]
                best = None
                if safe_cards: best = max(safe_cards)
                else:
                    if self.deck: best = min(self.deck)
                if best: 
                    self.deck.remove(best)
                    h.append(best)
            
            # 3. 无论是否被锁，这张卡都会留在桌面上提供 +10 伤害 (由函数开头的 append 处理)

        # === 【新增】幸福 (HAPPINESS) ===
        elif ctype == "HAPPINESS":
            # 双方各抽 1 张王牌
            self.give_trump(1, 1)
            self.give_trump(2, 1)

        # === 【新增】诅咒 (CURSE) ===
        elif ctype == "CURSE":
            # 1. 【代价】随机放弃一张自己的王牌
            # 获取剩余王牌列表 (这张 Curse 已经被移出去了)
            current_trumps = self.p1_trumps if pid == 1 else self.p2_trumps
            if len(current_trumps) > 0:
                # 随机选一个倒霉蛋移除
                rand_idx = random.randint(0, len(current_trumps) - 1)
                current_trumps.pop(rand_idx)
            
            # 2. 【效果】迫使对手抽出数字最大的牌
            # 检查封锁：如果我放了“生死一搏(GAMBLE)”，对手被禁抽，那我也不能强迫他抽
            my_gamble = False
            for t in self.active_trumps:
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == pid:
                    my_gamble = True
                    break
            
            if not my_gamble:
                opp_pid = 3 - pid
                opp_hand = self.p1_hand if opp_pid == 1 else self.p2_hand
                if sum(opp_hand) <= self.target_score:
                    if self.deck:
                        # 找出牌堆里数值最大的牌 (最危险的牌)
                        worst_card = max(self.deck)
                        self.deck.remove(worst_card)
                        opp_hand.append(worst_card)
        # === 【新增】魔抽 (MAGIC_DRAW) ===
        elif ctype == "MAGIC_DRAW":
            # 直接给当前玩家发 3 张王牌
            # give_trump 函数内部会自动处理上限(MAX_TRUMPS)，不用担心溢出
            self.give_trump(pid, 3)

         # === 【新增】遗忘 (OBLIVION) ===
        elif ctype == "OBLIVION":
            # 直接调用重置回合函数
            # 这会清空桌面、清空手牌、round_id +1、重新发牌
            # 从而完全跳过本回合的 RESULT (结算) 阶段
            self.reset_round()

        # === 【新增】终极抽牌 (ULTIMATE_DRAW) ===
        elif ctype == "ULTIMATE_DRAW":
            # 1. 抽 2 张王牌 (资源补充，无视封锁)
            self.give_trump(pid, 2)
            
            # 2. 抽最有利的牌 (同 PERFECT 逻辑，受封锁限制)
            opp_pid = 3 - pid
            is_locked = False
            for t in self.active_trumps:
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid:
                    is_locked = True
                    break
            
            if not is_locked:
                h = self.p1_hand if pid==1 else self.p2_hand
                cur = sum(h)
                # 计算安全卡
                safe_cards = [c for c in self.deck if cur + c <= self.target_score]
                best = None
                if safe_cards:
                    best = max(safe_cards) # 贪婪策略：尽可能接近目标分
                else:
                    if self.deck: best = min(self.deck) # 苟活策略：尽可能不死
                
                if best: 
                    self.deck.remove(best)
                    h.append(best)
          # === 【新增】增加二+ (RETURN_PLUS) ===
        elif ctype == "RETURN_PLUS":
            # 效果：将对手上一张面朝上的牌退回牌组
            opp_hand = self.p2_hand if pid == 1 else self.p1_hand
            
            # 对手必须至少有2张牌才能退（因为第1张是暗牌，不能退）
            # "面朝上的牌" = index >= 1 的牌
            if len(opp_hand) > 1:
                # 弹出最后一张（它是必定面朝上的）
                card = opp_hand.pop()
                # 插入回牌组随机位置
                self.deck.insert(random.randint(0, len(self.deck)), card)
            
            # 这张卡本身会留在场上 (active_trumps)，提供 +2 伤害 (由 calculate_potential_damage 处理)

        # === 【新增】完美抽牌+ (PERFECT_PLUS) ===
        elif ctype == "PERFECT_PLUS":
            # 效果：抽最有利的牌
            # 必须检查封锁 (Gamble/Silence)
            opp_pid = 3 - pid
            is_locked = False
            for t in self.active_trumps:
                if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid:
                    is_locked = True
                    break
            
            if not is_locked:
                # 复用 Perfect 逻辑
                h = self.p1_hand if pid==1 else self.p2_hand
                cur = sum(h)
                safe_cards = [c for c in self.deck if cur + c <= self.target_score]
                best = None
                if safe_cards: best = max(safe_cards)
                else: 
                    if self.deck: best = min(self.deck)
                
                if best: 
                    self.deck.remove(best)
                    h.append(best)
            
            # 这张卡本身会留在场上，提供 +5 伤害

        # === 【新增】王牌变换+ (TRUMP_EXCHANGE_PLUS) ===
        elif ctype == "TRUMP_EXCHANGE_PLUS":
            # 效果：随机放弃一张王牌，然后抽4张
            
            # 1. 代价：随机弃一张 (这张 Trump++ 已经离手了，不算在内)
            current_trumps = self.p1_trumps if pid == 1 else self.p2_trumps
            if len(current_trumps) > 0:
                rand_idx = random.randint(0, len(current_trumps) - 1)
                current_trumps.pop(rand_idx)
            
            # 2. 收益：抽 4 张
            self.give_trump(pid, 4)
            
            # 3. 既然这张卡没有持续效果，按你的要求，我**不**把它加到 instant_cards 清理列表里
            # 它会留在场上占一个格子（就像一张废纸），除非你自己后续统一清理。
            # 如果你想让它用完即消，请自行在 instant_cards 里加 "TRUMP_EXCHANGE_PLUS"
            
        return ctype

# --- 网络工具 ---
def send_msg(sock, data):
    if not sock: return False
    try:
        raw = pickle.dumps(data)
        sock.sendall(struct.pack('>I', len(raw)) + raw)
        return True
    except: return False

def recv_msg(sock):
    if not sock: return None
    try:
        raw_len = b""
        while len(raw_len) < 4:
            chunk = sock.recv(4 - len(raw_len))
            if not chunk: return None
            raw_len += chunk
        msg_len = struct.unpack('>I', raw_len)[0]
        
        raw_data = b""
        while len(raw_data) < msg_len:
            chunk = sock.recv(msg_len - len(raw_data))
            if not chunk: return None
            raw_data += chunk
        return pickle.loads(raw_data)
    except: return None

# --- 服务端 ---
def server_worker():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: server.bind(('0.0.0.0', DEFAULT_PORT)); server.listen(2)
    except: return

    clients = []
    gs = GameState()
    
    while len(clients) < 2:
        try:
            conn, addr = server.accept()
            conn.settimeout(None)
            pid = len(clients) + 1
            send_msg(conn, f"ID:{pid}")
            clients.append(conn)
        except: break
    
    # === 【新增 1】记录上一个回合是谁 ===
    last_turn_recorded = gs.turn 
    # =================================

    running = True
    while running:
        try:
            if gs.phase == "RESULT" and time.time() > gs.result_timer:
                if gs.p1_fingers <= 0 or gs.p2_fingers <= 0 or gs.is_escape_end: 
                    gs.phase = "GAMEOVER"
                else: 
                    gs.reset_round()
                     # === 【新增 2】新回合开始，重置记录 ===
                    last_turn_recorded = gs.turn
                    # ===================================
                # === 【新增 3】检测回合切换并执行清理 ===
            if gs.phase == "ACTION":
                # 如果当前行动者 (gs.turn) 与记录的 (last_turn_recorded) 不一样
                # 说明刚刚发生了回合交替（例如 P1 结束 -> 轮到 P2）
                if gs.turn != last_turn_recorded:
                    # 按照你的逻辑：轮到谁行动，就清理谁桌面上的一次性废卡
                    gs.cleanup_player_instants(gs.turn)
                    
                    # 更新记录
                    last_turn_recorded = gs.turn
            # ========================================
            
            send_msg(clients[0], gs)
            send_msg(clients[1], gs)
            
            rlist, _, _ = select.select(clients, [], [], 0)
            for conn in rlist:
                try:
                    pid = clients.index(conn) + 1
                    msg_obj = recv_msg(conn) 
                    if not msg_obj or not isinstance(msg_obj, str): continue
                    
                    now = time.time()
                    if now - gs.last_action_time[pid] < 0.5: continue
                    
                    parts = msg_obj.split(":")
                    cmd = parts[0]
                                                
                    if len(parts) > 1:
                        try:
                            cmd_rid = int(parts[-1])
                            if cmd_rid != gs.round_id: continue 
                        except: pass
                    
                    if gs.phase == "GAMEOVER":
                        if cmd == "REMATCH":
                            if pid == 1: gs.p1_req_rematch = True
                            else: gs.p2_req_rematch = True
                            if gs.p1_req_rematch and gs.p2_req_rematch: gs.full_reset()

                    
                    if gs.phase == "ACTION":
                        is_bust = gs.check_bust(pid)
                        
                        if gs.turn == pid:
                            action_taken = False
                            
                            if cmd == "HIT":
                                # --- 【新增】检查生死一搏封锁 ---
                                opp_pid = 3 - pid
                                is_draw_locked = False
                                for t in gs.active_trumps:
                                    # 【修改点】只要是 GAMBLE 或者 SILENCE，都算封锁
                                    if t['type'] in ['GAMBLE', 'SILENCE'] and t['owner'] == opp_pid:
                                        is_draw_locked = True
                                        break
                                
                                if is_draw_locked:
                                    continue # 被锁死，点击无效
                                # -----------------------------
                                       
                                if not is_bust:
                                    gs.last_action_time[pid] = now
                                    gs.draw_card(pid)
                                    if pid == 1: gs.p1_stop = False
                                    else: gs.p2_stop = False
                                    action_taken = True
                                
                            elif cmd == "STAY":
                                gs.last_action_time[pid] = now
                                if pid == 1: gs.p1_stop = True
                                else: gs.p2_stop = True
                                action_taken = True
                                                            
                            elif cmd == "TRUMP":
                                # --- 【修正后的严格上限检查】 ---
                                idx = int(parts[1])
                                
                                # 1. 获取手牌信息
                                trumps_list = gs.p1_trumps if pid == 1 else gs.p2_trumps
                                if idx < len(trumps_list):
                                    card_to_use = trumps_list[idx]
                                    card_type = card_to_use[1]
                                    
                                    # 2. 计算自己已占用的卡槽
                                    current_on_board = [t for t in gs.active_trumps if t['owner'] == pid]
                                    
                                    # 3. 满员检查
                                    if len(current_on_board) >= MAX_TABLE_SLOTS:
                                        is_allowed = False
                                        
                                        # 【情况 A】盾牌攻击 (SHIELD_ATTACK)
                                        # 只有当场上有足够护盾供其消耗时，它才能起到清理作用
                                        # 简单起见，只要是这个类型就放行，具体能不能发动的逻辑交给 use_trump 去判断
                                        # (因为 Shield Attack 发动失败也不会占格子，它只是不生效)
                                        if card_type in ["SHIELD_ATTACK", "SHIELD_ATTACK_PLUS"]:
                                            is_allowed = True
                                            
                                        # 【情况 B】目标卡 (TARGET)
                                        # 只有当场上【已经有】属于自己的 TARGET 卡时，才允许打出新的来【替换】
                                        # 效果：旧Target消失(-1)，新Target进场(+1)，总量不变，允许操作
                                        elif card_type == "TARGET":
                                            for t in current_on_board:
                                                if t['type'] == 'TARGET':
                                                    is_allowed = True
                                                    break
                                        
                                        # 【情况 C】遗忘 (OBLIVION)
                                        # 直接重置回合，清空全场，绝对允许
                                        elif card_type == "OBLIVION":
                                            is_allowed = True
                                            
                                        # 其他所有卡 (包括 Destroy, Draw, Add 等) 都会导致数量+1，全部禁止
                                        
                                        if not is_allowed:
                                            continue # 拦截操作
                                # ---------------------------------------

                                # --- 【保留：破坏++ 的封锁检查】 ---
                                opp_pid = 3 - pid
                                is_blocked = False
                                for t in gs.active_trumps:
                                    if t['type'] == 'DESTROY_BLOCK' and t['owner'] == opp_pid:
                                        is_blocked = True
                                        break
                                
                                if is_blocked:
                                    action_taken = False 
                                    continue 
                                # --------------------------------

                                gs.last_action_time[pid] = now
                                ctype = gs.use_trump(pid, idx)
                                
                                # === 收割机制 (HARVEST) ===
                                has_harvest = False
                                for t in gs.active_trumps:
                                    if t['type'] == 'HARVEST' and t['owner'] == pid:
                                        has_harvest = True
                                        break
                                if has_harvest:
                                    gs.give_trump(pid, 1)
                                # =========================

                                # 反制机制
                                opp = 3 - pid
                                if opp == 1: gs.p1_stop = False
                                else: gs.p2_stop = False
                                
                                action_taken = False
                            
                            elif cmd == "DISCARD":
                                try:
                                    idx = int(parts[1])
                                    # 执行弃牌
                                    gs.discard_trump(pid, idx)
                                    
                                    # 弃牌视作一次操作更新（刷新最后操作时间）
                                    gs.last_action_time[pid] = now
                                    
                                    # 强制刷新客户端状态（解除对方的等待状态）
                                    if pid == 1: gs.p1_stop = False
                                    else: gs.p2_stop = False
                                    
                                    # 关键：弃牌不消耗回合数，action_taken 设为 False
                                    # 这样玩家弃牌后，依然是他的回合，可以选择继续弃牌、使用牌或抽牌
                                    action_taken = False
                                except: pass

                            if action_taken:
                                gs.turn = 3 - pid
                            
                        if gs.p1_stop and gs.p2_stop: gs.resolve_round()
                except: pass
            time.sleep(0.02)
        except Exception as e:
            traceback.print_exc() # 在服务端控制台打印错误，但不退出游戏
            # running = False # 不要这句，防止因为一个小bug导致掉线
            continue
    
    try:
        server.close()
        for c in clients: c.close()
    except: pass

# --- UI ---
def draw_text(surf, font, text, x, y, color=C_TEXT_MAIN, align="left", shadow=True):
    if shadow:
        s = font.render(text, True, (0,0,0))
        sr = s.get_rect()
        if align=="center": sr.center=(x+2, y+2)
        else: sr.topleft=(x+2, y+2)
        surf.blit(s, sr)
    t = font.render(text, True, color)
    tr = t.get_rect()
    if align=="center": tr.center=(x, y)
    else: tr.topleft=(x, y)
    surf.blit(t, tr)

def draw_card(surf, font, val, x, y, hidden=False):
    rect = pygame.Rect(x, y, 90, 130)
    if hidden:
        pygame.draw.rect(surf, C_CARD_BACK, rect)
        pygame.draw.rect(surf, (10,10,10), rect, 2)
    else:
        pygame.draw.rect(surf, C_CARD_BG, rect)
        pygame.draw.rect(surf, (10,10,10), rect, 2)
        pygame.draw.circle(surf, (200, 190, 180), (x+20, y+20), 15)
        c = (180, 0, 0) if val >= 8 else (20, 20, 20)
        draw_text(surf, font, str(val), x+45, y+65, c, "center", False)

def draw_fingers(surf, current_fingers, x, y, label,max_hp_val):
    font = pygame.font.SysFont("arial", 18, bold=True)
    draw_text(surf, font, label, x, y-20, C_TEXT_DIM)
    for i in range(max_hp_val):
        bx = x + i * 26 
        by = y
        w, h = 22, 34
        if i < current_fingers:
            pygame.draw.rect(surf, C_FINGER_ALIVE, (bx, by, w, h))
            pygame.draw.rect(surf, (0,0,0), (bx, by, w, h), 1)
        else:
            pygame.draw.rect(surf, C_FINGER_DEAD, (bx, by, w, h))
            pygame.draw.rect(surf, (50,20,20), (bx, by, w, h), 1)
            pygame.draw.line(surf, C_FINGER_X, (bx+2, by+2), (bx+w-2, by+h-2), 2)
            pygame.draw.line(surf, C_FINGER_X, (bx+w-2, by+2), (bx+2, by+h-2), 2)

# --- 客户端 ---
class GameClient:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        try: pygame.scrap.init()
        except: pass

        self.clock = pygame.time.Clock()
        
        pygame.display.set_caption("RE7: 21 - SURVIVAL (v4.0 Final)")
        
        self.f_h = pygame.font.SysFont("impact", 60)
        self.f_l = pygame.font.SysFont("impact", 36)
        self.f_m = pygame.font.SysFont("arial", 20, bold=True)
        self.f_s = pygame.font.SysFont("arial", 12)
        
        self.scan = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        for y in range(0, SCREEN_HEIGHT, 4):
            pygame.draw.line(self.scan, (0,0,0,30), (0,y), (SCREEN_WIDTH,y))
            
        self.state = "MENU"
        self.input_ip = DEFAULT_HOST_IP
        self.err_msg = ""
        self.sock = None
        self.my_id = 0
        self.gs = None
        self.running = True
        self.waiting_for_p2 = False
        
        self.cursor_visible = True
        self.cursor_timer = 0
        self.btn_cooldown = 0
        self.local_rematch_clicked = False
        self.discard_mode = False

    def close_connection(self):
        if self.sock:
            try: self.sock.close()
            except: pass
            self.sock = None

    def safe_send_cmd(self, cmd_str):
        if self.sock and self.gs:
            self.btn_cooldown = 20 
            payload = f"{cmd_str}:{self.gs.round_id}"
            send_msg(self.sock, payload)

    def connect_to_host(self, ip):
        self.close_connection()
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((ip, DEFAULT_PORT))
            
            id_data = recv_msg(self.sock)
            if id_data and isinstance(id_data, str) and id_data.startswith("ID:"):
                self.my_id = int(id_data.split(":")[1])
                self.sock.settimeout(None)
                self.state = "GAME"
                self.waiting_for_p2 = True
                self.err_msg = ""
                
                while self.running:
                    data = recv_msg(self.sock)
                    if data:
                        if isinstance(data, GameState):
                            self.gs = data
                            self.waiting_for_p2 = False
                            if self.gs.phase == "ACTION":
                                self.local_rematch_clicked = False
                    else: break
            else: raise Exception("Handshake Failed")
        except Exception as e:
            self.err_msg = f"Connection Failed: {str(e)}"
            self.state = "MENU"
        finally: self.close_connection()

    def start_host(self):
        t = threading.Thread(target=server_worker)
        t.daemon = True; t.start()
        time.sleep(0.5)
        threading.Thread(target=self.connect_to_host, args=('127.0.0.1',), daemon=True).start()

    def start_join(self):
        self.state = "CONNECTING"
        threading.Thread(target=self.connect_to_host, args=(self.input_ip,), daemon=True).start()

    def run(self):
        try: self.main_loop()
        except Exception: self.show_error_screen(traceback.format_exc())

    def main_loop(self):
        while self.running:
            mx, my = pygame.mouse.get_pos()
            dt = self.clock.tick(30)
            self.screen.fill(C_BG)
            
            self.cursor_timer += dt
            if self.cursor_timer >= 500:
                self.cursor_visible = not self.cursor_visible
                self.cursor_timer = 0
            
            if self.btn_cooldown > 0:
                self.btn_cooldown -= 1

            if self.state == "MENU":
                draw_text(self.screen, self.f_h, "RE7: 21 SURVIVAL", SCREEN_WIDTH//2, 150, C_HIGHLIGHT, "center")
                host_rect = pygame.Rect(SCREEN_WIDTH//2 - 100, 300, 200, 60)
                c_h = C_BTN_HOVER if host_rect.collidepoint(mx,my) else C_BTN_IDLE
                pygame.draw.rect(self.screen, c_h, host_rect)
                pygame.draw.rect(self.screen, (100,100,100), host_rect, 2)
                draw_text(self.screen, self.f_m, "HOST GAME", host_rect.centerx, host_rect.centery, (255,255,255), "center", False)

                box_rect = pygame.Rect(SCREEN_WIDTH//2 - 150, 400, 300, 40)
                pygame.draw.rect(self.screen, (20,20,20), box_rect)
                pygame.draw.rect(self.screen, C_HIGHLIGHT, box_rect, 2)
                txt = self.input_ip + ("|" if self.cursor_visible else "")
                draw_text(self.screen, self.f_m, txt, SCREEN_WIDTH//2, 420, C_TEXT_MAIN, "center")
                draw_text(self.screen, self.f_s, "Enter Host IP (Supports Ctrl+V):", SCREEN_WIDTH//2, 380, C_TEXT_DIM, "center")

                join_rect = pygame.Rect(SCREEN_WIDTH//2 - 100, 460, 200, 60)
                c_j = C_BTN_HOVER if join_rect.collidepoint(mx,my) else C_BTN_IDLE
                pygame.draw.rect(self.screen, c_j, join_rect)
                pygame.draw.rect(self.screen, (100,100,100), join_rect, 2)
                draw_text(self.screen, self.f_m, "JOIN GAME", join_rect.centerx, join_rect.centery, (255,255,255), "center", False)

                if self.err_msg: draw_text(self.screen, self.f_s, self.err_msg, SCREEN_WIDTH//2, 600, C_ERROR, "center")

                for e in pygame.event.get():
                    if e.type == pygame.QUIT: self.running = False
                    if e.type == pygame.MOUSEBUTTONDOWN:
                        if host_rect.collidepoint(mx,my): self.start_host()
                        if join_rect.collidepoint(mx,my): self.start_join()
                    if e.type == pygame.KEYDOWN:
                        if e.key == pygame.K_v and (e.mod & pygame.KMOD_CTRL):
                            try:
                                t = pygame.scrap.get(pygame.SCRAP_TEXT).decode('utf-8').strip()
                                self.input_ip = "".join([c for c in t if c.isdigit() or c=='.'])[:15]
                            except: pass
                        elif e.key == pygame.K_BACKSPACE: self.input_ip = self.input_ip[:-1]
                        else:
                            if len(self.input_ip)<16 and (e.unicode.isdigit() or e.unicode=='.'): self.input_ip += e.unicode

            elif self.state == "CONNECTING":
                draw_text(self.screen, self.f_l, "CONNECTING...", SCREEN_WIDTH//2, SCREEN_HEIGHT//2, C_TEXT_MAIN, "center")
                for e in pygame.event.get():
                    if e.type == pygame.QUIT: self.running = False

            elif self.state == "GAME":
                trump_rects = [] 
                
                if self.waiting_for_p2 or not self.gs:
                    draw_text(self.screen, self.f_h, "WAITING FOR OPPONENT...", SCREEN_WIDTH//2, SCREEN_HEIGHT//2, C_TEXT_DIM, "center")
                    draw_text(self.screen, self.f_m, f"You are Player {self.my_id}", SCREEN_WIDTH//2, SCREEN_HEIGHT//2 + 50, C_TEXT_DIM, "center")
                else:
                    gs = self.gs
                    # Top Info
                    tar_col = C_FINGER_ALIVE if gs.target_score != 21 else C_TEXT_MAIN
                    draw_text(self.screen, self.f_l, f"TARGET: {gs.target_score}", SCREEN_WIDTH//2, 20, tar_col, "center")
                    
                    # ATK / DEF UI
                    out_dmg = gs.calculate_potential_damage(3 - self.my_id) 
                    in_dmg = gs.calculate_potential_damage(self.my_id)      
                    
                    pygame.draw.rect(self.screen, (40,10,10), (900, 20, 100, 70))
                    pygame.draw.rect(self.screen, C_FINGER_ALIVE, (900, 20, 100, 70), 2)
                    draw_text(self.screen, self.f_m, "ATK / DEF", 950, 35, C_HIGHLIGHT, "center")
                    draw_text(self.screen, self.f_l, f"{out_dmg} / {in_dmg}", 950, 55, C_TEXT_MAIN, "center")

                    # Opponent
                    opp_hand = gs.p2_hand if self.my_id == 1 else gs.p1_hand
                    opp_f = gs.p2_fingers if self.my_id == 1 else gs.p1_fingers
                    draw_fingers(self.screen, opp_f, 80, 80, "OPPONENT", gs.max_hp_limit)
                    
                    # === 【修改开始】对手点数显示 (红绿变色版) ===
                    opp_score_str = ""
                    opp_sum = sum(opp_hand) if opp_hand else 0
                    
                    # 默认颜色 (黄色)
                    opp_color = C_HIGHLIGHT 

                    if opp_hand:
                        # 如果处于行动阶段，第一张牌是暗牌
                        if gs.phase == "ACTION":
                            # 计算除了第一张牌以外的总和
                            vis_sum = sum(opp_hand[1:])
                            opp_score_str = f"X + {vis_sum} / {gs.target_score}"
                            
                             # === 【修改这里】 ===
                            # 如果光是明牌就已经爆了 (比如 X + 24)，直接标红
                            if vis_sum > gs.target_score:
                                opp_color = C_ERROR
                            else:
                                opp_color = C_HIGHLIGHT 
                            # ==================
                        else:
                            # 结算/游戏结束阶段，全显示
                            opp_score_str = f"{opp_sum} / {gs.target_score}"
                            
                            # === 这里加入颜色判断 ===
                            if opp_sum > gs.target_score:
                                opp_color = C_ERROR   # 爆牌变红 (和你的 fingers 死亡色一致)
                            elif opp_sum == gs.target_score:
                                opp_color = C_GREEN   # 完美目标变绿
                            else:
                                opp_color = C_HIGHLIGHT # 普通分保持高亮
                    else:
                        opp_score_str = f"0 / {gs.target_score}"
                        opp_color = C_HIGHLIGHT
                    
                    # 绘制对手分数 (使用计算好的颜色)
                    draw_text(self.screen, self.f_l, opp_score_str, 400, 85, opp_color)
                    # ==================================================

                    for i, c in enumerate(opp_hand):
                        is_hid = (i==0 and gs.phase=="ACTION")
                        draw_card(self.screen, self.f_l, c, 80 + i*100, 120, is_hid)

                    # === 【UI修改区域】场上王牌区域 (向左微调版) ===
                    
                    # 1. 背景框调整
                    # 向左延伸了约0.8张牌的距离
                    box_x = 90   
                    box_y = 260
                    box_w = 770
                    box_h = 120
                    
                    pygame.draw.rect(self.screen, (20,20,25), (box_x, box_y, box_w, box_h))
                    pygame.draw.rect(self.screen, (60,60,60), (box_x, box_y, box_w, box_h), 1)
                    
                    # 2. 中间的分界线
                    pygame.draw.line(self.screen, (50,50,50), (box_x, box_y + 60), (box_x + box_w, box_y + 60), 1)
                    
                    # 3. 绘制场上的卡牌
                    opp_cnt = 0; my_cnt = 0
                    
                    # 起始位置：紧贴框左侧
                    card_start_x = box_x + 10 
                    card_step_x = 90 
                    
                    for t in gs.active_trumps:
                        is_mine = (t['owner'] == self.my_id)
                        col = (80, 180, 80) if is_mine else (180, 80, 80)
                        
                        if is_mine:
                            # 下半部分 (我的)
                            tx = card_start_x + my_cnt * card_step_x
                            ty = box_y + 65 
                            my_cnt += 1
                        else:
                            # 上半部分 (对手的)
                            tx = card_start_x + opp_cnt * card_step_x
                            ty = box_y + 5 
                            opp_cnt += 1
                            
                        pygame.draw.rect(self.screen, (40,40,40), (tx, ty, 80, 50))
                        pygame.draw.rect(self.screen, col, (tx, ty, 80, 50), 1)
                        draw_text(self.screen, self.f_s, t['name'], tx+40, ty+25, C_TEXT_MAIN, "center")
                        
                    # =============================================
                    # Player
                    my_hand = gs.p1_hand if self.my_id == 1 else gs.p2_hand
                    my_f = gs.p1_fingers if self.my_id == 1 else gs.p2_fingers
                    my_sum = sum(my_hand)
                    for i, c in enumerate(my_hand):
                        draw_card(self.screen, self.f_l, c, 80 + i*100, 430, False)
                    draw_fingers(self.screen, my_f, 80, 580, "YOU",gs.max_hp_limit)
                     # === 【修改】玩家点数显示 (原有位置，新格式) ===
                    # 原代码: draw_text(self.screen, self.f_l, f"SUM: {my_sum}", 400, 590, C_TEXT_MAIN)
                    # 新代码: 显示 "当前点数 / 目标点数"
                    
                    my_score_color = C_GREEN if my_sum == gs.target_score else C_TEXT_MAIN
                    if my_sum > gs.target_score: my_score_color = C_ERROR # 爆牌显示红色
                    
                    draw_text(self.screen, self.f_l, f"{my_sum} / {gs.target_score}", 400, 590, my_score_color)
                    # =============================================
                    
                    if gs.phase == "RESULT":
                        s = pygame.Surface((SCREEN_WIDTH, 100)); s.set_alpha(230); s.fill((0,0,0))
                        self.screen.blit(s, (0, 330))
                        if gs.round_winner == 0: msg = "DRAW"; col = (200, 200, 200)
                        elif gs.round_winner == self.my_id: msg = f"YOU WON! (OPP LOST {gs.round_damage} FINGERS)"; col = (50, 255, 50)
                        else: msg = f"YOU LOST! (-{gs.round_damage} FINGERS)"; col = (255, 50, 50)
                        draw_text(self.screen, self.f_l, msg, SCREEN_WIDTH//2, 380, col, "center")
                    
                    elif gs.phase == "GAMEOVER":
                        self.screen.fill((0,0,0))
                        win = (my_f > 0)
                        # === 【修改】自定义逃脱结局的文字 ===
                        if gs.is_escape_end:
                            msg = "GAME ENDED (ESCAPED!)"
                            col = (100, 200, 255) # 亮蓝色，代表和平
                        else:
                            msg = "YOU SURVIVED" if win else "YOU DIED"
                            col = C_GREEN if win else C_ERROR
                        # ==================================
                        draw_text(self.screen, self.f_h, msg, SCREEN_WIDTH//2, SCREEN_HEIGHT//2 - 50, col, "center")
                        
                        restart_rect = pygame.Rect(SCREEN_WIDTH//2 - 125, SCREEN_HEIGHT//2 + 50, 250, 60)
                        
                        i_req = gs.p1_req_rematch if self.my_id==1 else gs.p2_req_rematch
                        opp_req = gs.p2_req_rematch if self.my_id==1 else gs.p1_req_rematch
                        
                        if self.local_rematch_clicked or i_req:
                            btn_color = C_BTN_DISABLED
                            btn_text = "WAITING..."
                        elif opp_req:
                            btn_color = C_HIGHLIGHT if restart_rect.collidepoint(mx,my) else C_GREEN
                            btn_text = "ACCEPT MATCH!"
                        else:
                            btn_color = C_BTN_HOVER if restart_rect.collidepoint(mx,my) else C_BTN_IDLE
                            btn_text = "PLAY AGAIN"
                            
                        pygame.draw.rect(self.screen, btn_color, restart_rect)
                        pygame.draw.rect(self.screen, (200,200,200), restart_rect, 2)
                        draw_text(self.screen, self.f_m, btn_text, restart_rect.centerx, restart_rect.centery, (255,255,255), "center", False)

                    elif gs.phase == "ACTION":
                        if gs.turn == self.my_id:
                            draw_text(self.screen, self.f_h, "YOUR TURN", SCREEN_WIDTH//2, 400, C_HIGHLIGHT, "center", shadow=True)
                            opp = 3 - self.my_id
                            if (gs.p1_stop if opp==1 else gs.p2_stop):
                                draw_text(self.screen, self.f_s, "OPPONENT STAYED", SCREEN_WIDTH//2, 435, C_FINGER_ALIVE, "center")
                        else:
                            if (gs.p1_stop if self.my_id==1 else gs.p2_stop):
                                draw_text(self.screen, self.f_l, "YOU STAYED", SCREEN_WIDTH//2, 400, C_TEXT_DIM, "center", shadow=True)
                            else:
                                draw_text(self.screen, self.f_l, "WAITING...", SCREEN_WIDTH//2, 400, C_TEXT_DIM, "center", shadow=True)

                    # Buttons
                    is_turn = (gs.turn == self.my_id)
                    is_bust = my_sum > gs.target_score
                    can_act = is_turn and self.btn_cooldown == 0
                    
                    can_hit = can_act and not is_bust
                    can_stay = can_act
                    c_hit = C_HIGHLIGHT if can_hit else C_BTN_DISABLED
                    c_stay = (200, 60, 60) if can_stay else C_BTN_DISABLED
                    
                    pygame.draw.rect(self.screen, c_hit, (800, 480, 150, 55))
                    draw_text(self.screen, self.f_m, "HIT", 875, 507, (0,0,0), "center", False)
                    pygame.draw.rect(self.screen, c_stay, (800, 550, 150, 55))
                    draw_text(self.screen, self.f_m, "STAY", 875, 577, (0,0,0), "center", False)
                    
                    # === 【新增】绘制弃牌开关按钮 ===
                    # 位置：放在 HIT 按钮上方 (x=800, y=410)
                    discard_rect = pygame.Rect(800, 410, 150, 40)
                    
                    # 颜色逻辑：开启显示红色警示，关闭显示灰色
                    if self.discard_mode:
                        c_disc = (200, 50, 50) # 亮红
                        btn_txt = "DISCARD: ON"
                    else:
                        c_disc = (60, 60, 70) # 与其他按钮同色
                        btn_txt = "DISCARD MODE"
                        
                    pygame.draw.rect(self.screen, c_disc, discard_rect)
                    pygame.draw.rect(self.screen, (200, 200, 200), discard_rect, 2)
                    draw_text(self.screen, self.f_m, btn_txt, discard_rect.centerx, discard_rect.centery, (255,255,255), "center", False)


                    if is_bust:
                        draw_text(self.screen, self.f_m, "BUSTED!", 875, 450, C_FINGER_ALIVE, "center")
                        draw_text(self.screen, self.f_s, "(Must Stay)", 875, 465, C_TEXT_DIM, "center")
                    elif (gs.p1_stop if self.my_id==1 else gs.p2_stop):
                        draw_text(self.screen, self.f_m, "STAYED", 875, 450, C_TEXT_DIM, "center")

                    my_trumps = gs.p1_trumps if self.my_id == 1 else gs.p2_trumps

                    for i, t in enumerate(my_trumps):
                        # === 【修改开始】支持双行显示逻辑 (13+13) ===
                        w = 70
                        h = 45
                        gap = 5
                        cards_per_row = 13  # 第一行13张，之后换行
                        
                        # 数学计算：
                        # row = 0 (第一行) 或 1 (第二行)
                        # col = 0 到 12 (列索引)
                        row = i // cards_per_row
                        col = i % cards_per_row
                        
                        # 计算坐标：
                        # X轴根据列数偏移
                        # Y轴根据行数偏移，第二行会向下移 50像素 (45+5)
                        tx = 40 + col * (w + gap)
                        ty = 660 + row * (h + gap)
                        
                        r = pygame.Rect(tx, ty, w, h)
                        trump_rects.append(r)
                        # === 【修改结束】坐标计算完毕，下面是画图逻辑(保持不变) ===
                        
                        bg = (100, 100, 100)
                        if t[1] == "ADD": bg = (160, 60, 60)
                        elif t[1] == "SHIELD": bg = (60, 60, 160)
                        elif t[1] == "TARGET": bg = (160, 160, 60)
                        
                        # === 【修改】如果处于弃牌模式，边框变红 ===
                        border_col = (255, 50, 50) if self.discard_mode else (0, 0, 0)
                        border_width = 3 if self.discard_mode else 2
                        
                        pygame.draw.rect(self.screen, bg, r)
                        pygame.draw.rect(self.screen, border_col, r, border_width) # 使用动态边框
                                        
                        # 绘制文字
                        draw_text(self.screen, self.f_s, t[0], r.centerx, r.centery, (0,0,0), "center", False)
                        
                    if len(my_trumps) >= MAX_TRUMPS:
                        # 如果满了，提示文字稍微往下移一点，避免挡住第二行
                        draw_text(self.screen, self.f_s, "MAX", 10, 680, C_FINGER_ALIVE)
                        
                self.screen.blit(self.scan, (0,0))

                for e in pygame.event.get():
                    if e.type == pygame.QUIT: self.running = False
                    if not self.waiting_for_p2 and self.gs and self.gs.phase == "ACTION":
                        if 'can_act' in locals() and can_act:
                            if e.type == pygame.KEYDOWN:
                                if e.key == pygame.K_SPACE and can_hit: self.safe_send_cmd("HIT")
                                if e.key == pygame.K_RETURN: self.safe_send_cmd("STAY")
                            if e.type == pygame.MOUSEBUTTONDOWN:
                                mx, my = pygame.mouse.get_pos()
                                # === 【新增 1】检查弃牌按钮点击 ===
                                if pygame.Rect(800, 410, 150, 40).collidepoint(mx, my):
                                    self.discard_mode = not self.discard_mode # 切换开关状态
                                # === 【修改 2】HIT/STAY 增加保护 (弃牌模式下不能点) ===
                                if pygame.Rect(800, 480, 150, 55).collidepoint(mx, my) and can_hit: 
                                    if not self.discard_mode: self.safe_send_cmd("HIT")
                                
                                if pygame.Rect(800, 550, 150, 55).collidepoint(mx, my): 
                                    if not self.discard_mode: self.safe_send_cmd("STAY")

                                # === 【修改 3】王牌点击逻辑分流 ===
                                for i, r in enumerate(trump_rects):
                                    if r.collidepoint(mx, my):
                                        if self.discard_mode:
                                            # 分支 A：弃牌模式 -> 发送 DISCARD
                                            self.safe_send_cmd(f"DISCARD:{i}")
                                            # 体验优化：弃一张后自动关闭开关，防止手滑连删
                                            self.discard_mode = False 
                                        else:
                                            # 分支 B：普通模式 -> 发送 TRUMP (使用)
                                            self.safe_send_cmd(f"TRUMP:{i}")
                        
                    elif self.gs and self.gs.phase == "GAMEOVER":
                        if e.type == pygame.MOUSEBUTTONDOWN:
                            mx, my = pygame.mouse.get_pos()
                            if not self.local_rematch_clicked and pygame.Rect(SCREEN_WIDTH//2 - 125, SCREEN_HEIGHT//2 + 50, 250, 60).collidepoint(mx, my):
                                self.local_rematch_clicked = True
                                self.safe_send_cmd("REMATCH")

            pygame.display.flip()
        
        self.close_connection()
        pygame.quit()
        sys.exit()

    def show_error_screen(self, error_text):
        while True:
            self.screen.fill((50, 0, 0))
            draw_text(self.screen, self.f_l, "CRITICAL ERROR (CRASH PREVENTED)", SCREEN_WIDTH//2, 100, (255,255,255), "center")
            lines = error_text.split('\n')
            y = 160
            for line in lines:
                if len(line) > 80:
                    draw_text(self.screen, self.f_s, line[:80], 50, y, (255,200,200), "left", False)
                    y += 20
                    draw_text(self.screen, self.f_s, line[80:], 50, y, (255,200,200), "left", False)
                else:
                    draw_text(self.screen, self.f_s, line, 50, y, (255,200,200), "left", False)
                y += 20
            draw_text(self.screen, self.f_m, "Press ESC to Exit", SCREEN_WIDTH//2, SCREEN_HEIGHT - 50, (255,255,255), "center")
            pygame.display.flip()
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    pygame.quit()
                    sys.exit()

if __name__ == "__main__":
    GameClient().run()