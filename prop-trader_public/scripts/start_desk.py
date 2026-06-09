import subprocess
import sys
import os
import time

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    
    print("="*60)
    print("🚀 IGNITING APEX-Q INSTITUTIONAL SWARM...")
    print("="*60)
    
    processes = []
    
    try:
        # 🚨 CHANGED BOOT LABEL TO 1-HOUR INTRADAY
        print("⚡ Booting V1 (1-Hour Intraday)...")
        p1 = subprocess.Popen([sys.executable, os.path.join(base_dir, "trader_v1.py")])
        processes.append(p1)
        time.sleep(1) 
        
        print("⚡ Booting V2 (4-Hour Swing)...")
        p2 = subprocess.Popen([sys.executable, os.path.join(base_dir, "trader_v2.py")])
        processes.append(p2)
        time.sleep(1)
        
        print("⚡ Booting Web Dashboard...")
        p3 = subprocess.Popen([sys.executable, os.path.join(base_dir, "apex_web_dashboard_new.py")])
        processes.append(p3)
        
        print("\n✅ All systems are fully online and scanning.")
        print("🛑 Press Ctrl+C at any time to safely shut down all engines.\n")
        
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n🛑 SHUTDOWN SEQUENCE INITIATED...")
        for p in processes:
            p.terminate()
        print("✅ All engines successfully powered down. Ledger saved. Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()