Poftim conținutul pentru entsoe.yml — selectează tot ce e între cele două linii cu ``` și copiază:

yaml
name: ENTSO-E live data (Romania)

on:
  schedule:
    - cron: "5 * * * *"   # la minutul 5 din fiecare oră, UTC
  workflow_dispatch: {}    # buton "Run workflow" pentru test manual

permissions:
  contents: write

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install requests

      - name: Fetch ENTSO-E data
        env:
          ENTSOE_TOKEN: ${{ secrets.ENTSOE_TOKEN }}
        run: python scripts/fetch_entsoe.py

      - name: Commit and push result
        run: |
          git config user.name "entsoe-bot"
          git config user.email "actions@users.noreply.github.com"
          git add entsoe-live.md
          git diff --quiet --cached || git commit -m "update entsoe-live.md"
          git push
