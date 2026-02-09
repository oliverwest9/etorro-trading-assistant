# Moltbook: Conversational PR Update Guide

## What is Moltbook?

Moltbook is our conversational style guide for pull request updates. Instead of formal, corporate-style updates, we embrace a more casual, Reddit-style communication approach that keeps everyone in the loop while maintaining a friendly, approachable tone.

## The Philosophy

Think of your PR as a conversation thread on Reddit. You're not writing a formal report - you're chatting with your teammates about what you're building, the challenges you're facing, and the progress you're making.

## Core Principles

### 1. **Keep it conversational**
Write like you're explaining things to a friend over coffee. Use "I", "we", and "you". It's okay to be casual!

❌ **Don't:** "Implementation of the authentication module has been completed."
✅ **Do:** "Hey! Just wrapped up the auth module. It was trickier than I thought, but it's working now!"

### 2. **One thread for everything**
Instead of scattering updates across multiple comments, keep everything in the PR description. Update it as you go, adding new sections as you make progress.

### 3. **Show your thinking**
Don't just say *what* you did - explain *why* you made certain decisions. Share the trade-offs you considered.

❌ **Don't:** "Changed the database schema."
✅ **Do:** "Changed the database schema because the old approach was causing N+1 queries. I considered using eager loading, but normalizing the data made more sense for our use case."

### 4. **Be transparent about challenges**
If you hit a roadblock or something took longer than expected, say so! It helps the team understand context and might surface better solutions.

### 5. **Ask questions early**
Not sure about something? Ask in your PR update! Don't wait until review time.

## Update Format

When you add an update to your PR, use this structure:

```markdown
---
**Update - [Date/Time]**

[Your conversational update here]

What I worked on:
- Thing 1
- Thing 2

What's next:
- Next step 1
- Next step 2

Heads up:
[Any blockers, questions, or things the team should know]

---
```

## Examples

### Example 1: Initial PR

```markdown
## Initial Post

**What's this PR about?**

Hey team! Working on adding support for crypto assets in our trading agent. Right now we only handle stocks, but the eToro API has great crypto endpoints we're not using.

**Why are we doing this?**

A few users asked about crypto support, and honestly, it's a pretty straightforward extension of what we already have. Plus, crypto markets are 24/7, so we could potentially run more frequent analysis cycles.

**What changed?**

- Added crypto instrument type to the database schema
- Extended the eToro client with crypto endpoints
- Updated the analysis engine to handle crypto price patterns
- Added tests for the new crypto flows

---
```

### Example 2: Mid-development Update

```markdown
---
**Update - Feb 9, 3:30pm**

Quick update! The database schema changes are done and tested. I went with a discriminator column approach rather than separate tables - seemed cleaner and the performance is fine for our scale.

One thing I'm noticing: crypto price volatility is way higher than stocks, so our trend detection algorithm is getting a bit noisy. I'm thinking we might need different threshold values for crypto vs stocks. What do y'all think?

For now, I've made the thresholds configurable so we can tune them without code changes. Will add some sensible defaults and document them.

What's next:
- Finish the report formatter to include crypto section
- Add integration test with real-ish data
- Update docs

---
```

### Example 3: Ready for Review

```markdown
---
**Update - Feb 9, 5:45pm**

Alright, I think we're good to go! 🚀

All tests passing, docs updated, and I ran it manually with some test data - looks solid. The crypto section in the report is formatted nicely and the recommendations make sense.

One note: I ended up using different volatility thresholds for crypto (2.5% vs 1.5% for stocks). Crypto just moves too fast otherwise. It's configurable in the config table if we want to tune it.

- [x] Code is complete
- [x] Tests pass
- [x] Documentation updated
- [x] Ready for review

Give it a look when you have a chance!

---
```

## Tips

- **Use emoji sparingly** - A 🚀 here and there is fine, but don't overdo it
- **Be specific** - "Fixed the bug" isn't helpful. "Fixed the null pointer exception in the price parser when handling after-hours data" is better
- **Time-box your updates** - Don't spend 20 minutes writing an update. Five minutes is plenty
- **Update as you go** - Don't wait until the PR is "done" to write everything up. Update it throughout development
- **Read the room** - Adjust your tone to match your team's culture. Some teams are super casual, others less so

## Why This Works

1. **Better context for reviewers** - They can see your thought process, not just the final code
2. **Easier to follow** - Everything is in one place, chronologically ordered
3. **More engaging** - Conversational updates are more fun to read than formal status reports
4. **Encourages collaboration** - Makes it easier for others to jump in with ideas or help

## Questions?

If you're not sure how to structure something or whether an update is too long/short/formal, just ask! The whole point is to make communication easier, not harder.

---

*This guide is inspired by how successful open source projects and tech communities communicate on platforms like Reddit and Hacker News.*
