# Moltbook System Demo

This file demonstrates how the Moltbook conversational PR update system works in practice.

## Scenario: Adding Crypto Support to the Trading Agent

This is what a PR following the Moltbook style would look like:

---

# Add crypto asset support to trading agent

## Initial Post

**What's this PR about?**

Hey team! Working on adding support for crypto assets in our trading agent. Right now we only handle stocks and ETFs, but the eToro API has some really solid crypto endpoints we're not using. This will let us track Bitcoin, Ethereum, and other major cryptocurrencies.

**Why are we doing this?**

Couple reasons:
- Had a few users ask about crypto support in the last sprint
- Crypto markets run 24/7, which means we could potentially run more frequent analysis cycles (not in this PR, but opens the door)
- The eToro API makes it pretty straightforward - their crypto endpoints follow the same patterns as stocks

**What changed?**

- Added `crypto` as a new instrument type in the database schema
- Extended the eToro client with crypto-specific endpoints
- Updated the analysis engine to handle crypto price patterns (they're way more volatile than stocks!)
- Added comprehensive tests for the crypto flows
- Updated report formatter to include a crypto section

## Progress Updates

---
**Update - Feb 9, 2:15pm**

Got the database schema changes done! Went with adding a `type` discriminator column to the instruments table rather than creating a separate crypto table. The data model is basically the same (symbol, price, volume, etc), so it felt cleaner to keep everything together.

One thing I learned: crypto exchanges use different symbol formats. eToro uses "BTC" but some other sources use "BTCUSD". Made sure our symbol normalization handles this correctly.

Running the migration now in my local dev environment... looks good!

---

**Update - Feb 9, 3:45pm**

Alright, ran into something interesting. Crypto price volatility is WAY higher than stocks - like, a 5% daily move in stocks is huge, but crypto does that before breakfast 😅

Our existing trend detection algorithm is getting super noisy with crypto. It keeps flagging every little dip as a "significant downtrend" when it's just normal crypto market stuff.

My solution: Made the volatility thresholds configurable per asset type. Stocks use 1.5%, crypto uses 2.5%. It's all in the config table now so we can tune without touching code.

Quick question for reviewers: Should we surface this in the report as "crypto-adjusted thresholds" or just use the different values silently? I'm leaning toward mentioning it so users understand why we're not freaking out over normal crypto volatility.

---

**Update - Feb 9, 5:30pm**

Tests are all green! ✅

Just finished the integration test with real-ish data (mocked API responses but using actual BTC/ETH price patterns from last week). The report looks really clean - crypto gets its own section and the recommendations make sense.

What's working well:
- Price action analysis correctly identifies crypto bull/bear patterns
- Volume analysis accounts for the 24/7 trading
- Report formatting keeps crypto separate from traditional assets (less clutter)

What I decided to punt on:
- Different analysis intervals for crypto (we're still doing 2x daily runs)
- Crypto-specific metrics like on-chain data (out of scope for MVP)
- Stablecoin handling (treating them like regular crypto for now)

---

**Update - Feb 9, 6:15pm**

Okay, I think we're ready to go! 🚀

Did a full manual test:
1. Added BTC and ETH to tracked instruments
2. Ran the agent
3. Report generated successfully with crypto section
4. Recommendations look sensible ("BTC showing bullish momentum, consider position" etc)

All acceptance criteria met:
- [x] Code is complete
- [x] Tests pass (100% coverage on new code)
- [x] Documentation updated (added crypto section to PLAN.md)
- [x] Manual testing done
- [x] Ready for review

Heads up for reviewers: The biggest change is in `src/agent/analysis/price_analyzer.py` where I added the volatility threshold logic. Everything else is pretty straightforward additions.

Give it a look when you get a chance!

---

## Questions & Discussion

### Q: Why not create a separate `crypto_instruments` table?

Good question! I considered it but decided against it because:
1. The data model is identical (symbol, price, volume, timestamp)
2. Our queries don't benefit from separation
3. Adding more asset types later (forex, commodities) would mean more tables
4. The discriminator pattern is well-tested and works great for this use case

If we find performance issues or the data models start diverging significantly, we can always refactor later.

### Q: What about gas fees and blockchain-specific stuff?

For this MVP, we're treating crypto purely as a tradable asset - same way we treat stocks. We're not doing any on-chain analysis or tracking gas fees. The eToro API abstracts all that away anyway since you're not actually buying crypto directly, you're buying CFDs on crypto prices.

If we want to add that depth later, it'd be a separate feature.

---

## Review Checklist

- [ ] Code reviewed
- [ ] Tests reviewed
- [ ] Documentation makes sense
- [ ] No security issues
- [ ] Ready to merge

---

*This PR uses Moltbook conversational updates - see `.github/MOLTBOOK.md` for the style guide!*

---

## How This Demonstrates Moltbook Principles

### ✅ Conversational Tone
Notice how the updates read like someone explaining their work to a teammate, not filing a formal report. Phrases like "Hey team!" and "way more volatile" and emoji usage keep it approachable.

### ✅ One Thread
Everything is in the PR description, organized chronologically. No hunting through scattered comments to understand the development journey.

### ✅ Shows Thinking
The author explains *why* decisions were made, not just what was changed. Example: The discussion about volatility thresholds and why a separate table wasn't needed.

### ✅ Transparent About Challenges
The author mentions hitting issues (noisy trend detection) and explains how they solved it. They also call out what they explicitly decided NOT to do and why.

### ✅ Asks Questions
Rather than waiting for review comments, the author proactively asks about surfacing crypto-adjusted thresholds in the report.

### ✅ Makes Review Easy
The final update summarizes what changed, confirms all criteria are met, and points reviewers to the most important file to examine.

This is what we're aiming for with Moltbook! 🎯
