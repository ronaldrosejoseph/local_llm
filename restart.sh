#!/bin/bash

# restart.sh - Restarts the LLM server

echo "Restarting LLM server..."
./stop.sh
./start.sh
