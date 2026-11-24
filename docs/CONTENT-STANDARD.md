# Content standard (summary)

`studio/content_standard.v1.json` is the machine contract that is added to every generation prompt, and `studio/editorial.py` applies a set of review gates to every model critique. Both are written in Turkish for one account (a Turkish-language account about practical AI agents for non-technical workers).

In short, the standard asks that each piece:

- does one clear job for the reader (notice a situation or risk, decide between options under a condition, take a small reversible step, or tell a claim's evidence from its limit);
- starts from a beginner depth by default and introduces few new terms;
- keeps source claims, verified facts, the author's own experience and editorial advice apart;
- uses first person only for the author's real opinion, decision or evidenced experience;
- stays honest about what was and was not tested.

Interpretation: if you reuse the app for another account or language, replace the standard JSON and the prompts in `studio/generator.py`; the revision, approval and packaging machinery does not depend on the Turkish text.
