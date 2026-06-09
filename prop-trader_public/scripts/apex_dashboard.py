import os
import time
import json
from datetime import datetime

# --- PATH CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
# Ensure we find the config file whether we run from root or the scripts/ folder
CONFIG_PATH = os.path.join(BASE_DIR, "trader_v1_config.json")
if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = os.path.join(os.path.dirname(BASE_DIR), "trader_v1_config.json")

def draw_dashboard():
    while True:
        # Clear the terminal screen for a live-updating feel
        os.system('clear' if os.name == 'posix' else 'cls')
        
        print("="*65)
        print("🧠 APEX-Q: LIVE STRATEGY DASHBOARD 🧠".center(65))
        print("="*65)
        
        if not os.path.exists(CONFIG_PATH):
            print("\n[!] Waiting for trader_v1.py to generate config JSON...\n")
        else:
            try:
                with open(CONFIG_PATH, 'r') as f:
                    data = json.load(f)
                
                # Get the exact time the AI wrote the file
                last_mod = os.path.getmtime(CONFIG_PATH)
                sync_time = datetime.fromtimestamp(last_mod).strftime('%Y-%m-%d %H:%M:%S BST')
                    
                print(f"\n🌍 MARKET REGIME:    {data.get('market_regime', 'N/A')}")
                print(f"🎯 ACTIVE STRATEGY:  {data.get('active_strategy', 'N/A')}")
                print(f"🧭 DIRECTIONAL BIAS: {data.get('directional_bias', 'N/A')}")
                print(f"💰 RISK PER TRADE:   {data.get('risk_per_trade_pct', 'N/A')}%")
                print(f"🛑 ACTION OVERRIDE:  {data.get('action_override', 'N/A')}")
                
                print("\n" + "-"*65)
                print("💡 APEX-Q LOGIC & REASONING:")
                
                # Word wrap the reasoning summary so it fits cleanly in the terminal
                reasoning = data.get('reasoning_summary', 'N/A')
                words = reasoning.split()
                lines = [' '.join(words[i:i+8]) for i in range(0, len(words), 8)]
                for line in lines:
                    print(f"   {line}")
                print("-" * 65 + "\n")
                print(f"⏱️  Brain Last Synced: {sync_time}")
                
            except json.JSONDecodeError:
                print("\n[!] Reading config... (JSON currently updating)\n")
                
        print(f"🔄 UI Last Refreshed:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S BST')}")
        print("Auto-refreshing every 5 seconds. Press Ctrl+C to close.")
        time.sleep(5)

if __name__ == "__main__":
    try:
        draw_dashboard()
    except KeyboardInterrupt:
        print("\nDashboard closed.")