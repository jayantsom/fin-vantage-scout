import re

with open('frontend/templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Update methodology cards to reflect new numbering and order.

# 1. Update text titles
html = html.replace('News Sentiment (LLM Engine)', 'News Sentiment (step1g)')
html = html.replace('Technical Metrics (step1d)', 'Technical Metrics (step1c)')
html = html.replace('Earnings Quality — Sloan Accruals (step1e)', 'Earnings Quality — Sloan Accruals (step1d)')
html = html.replace('Valuation Multiples (step1f)', 'Valuation Multiples (step1e)')
html = html.replace('Competitive Moat — Durability (step1g)', 'Competitive Moat — Durability (step1f)')

# 2. Extract the News Sentiment card block and move it after the Moat block
news_card_regex = r'(<!-- News Sentiment -->\s*<div class="methodology-card" id="doc-news">.*?</div>\s*<span class="methodology-card-badge badge-tech-langgraph">LangGraph Engine</span>\s*</div>)'
match = re.search(news_card_regex, html, flags=re.DOTALL)
if match:
    news_card = match.group(1)
    html = html.replace(news_card, '') # remove from current position
    
    # Insert after Moat
    moat_card_regex = r'(<!-- Moat -->\s*<div class="methodology-card" id="doc-moat">.*?</div>\s*<span class="methodology-card-badge badge-tech-langgraph">yfinance Annual Financials</span>\s*</div>)'
    moat_match = re.search(moat_card_regex, html, flags=re.DOTALL)
    if moat_match:
        moat_card = moat_match.group(1)
        html = html.replace(moat_card, moat_card + '\n\n            ' + news_card)

# 3. Cache buster for app.js
html = html.replace('src="/static/app.js"', 'src="/static/app.js?v=2"')

with open('frontend/templates/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('Updated index.html!')
