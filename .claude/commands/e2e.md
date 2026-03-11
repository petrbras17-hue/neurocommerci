Run end-to-end browser tests on the web application.

Steps:
1. Check if the FastAPI server is running (try curl localhost:8000/health)
2. If not running, start it in background
3. Launch the `e2e-tester` agent with Playwright MCP to test all web surfaces
4. Report results with screenshots
