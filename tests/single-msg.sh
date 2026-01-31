#!/bin/bash
curl -s -X POST http://localhost:9980/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": false}'
