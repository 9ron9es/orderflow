#!/bin/bash
# Monitor backtest progress

echo "🔍 Backtest Monitoring"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

while true; do
    if [ -f "/tmp/backtest.log" ]; then
        lines=$(wc -l < /tmp/backtest.log)
        echo "[$(date +'%H:%M:%S')] Log lines: $lines"
        
        # Show last few lines
        tail -5 /tmp/backtest.log | sed 's/^/  /'
        
        # Check if done
        if grep -q "Results saved" /tmp/backtest.log 2>/dev/null; then
            echo ""
            echo "✅ BACKTEST COMPLETE!"
            break
        fi
    fi
    
    # Check if process still running
    if ! ps aux | grep -v grep | grep -q "run_backtest_orderflow"; then
        echo "❌ Process died or completed"
        if [ -f "/tmp/backtest.log" ]; then
            tail -20 /tmp/backtest.log
        fi
        break
    fi
    
    sleep 5
    echo ""
done
