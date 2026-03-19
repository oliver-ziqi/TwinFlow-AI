# Vibe Coding Lessons Learned

This document is a reflection on how I used vibe coding while building a JaamSim-based logistics RCA system with digital twin visualization, event streaming, and human-in-the-loop decision support.

It is not meant to be a tutorial on prompts or tools. It is a summary of what I learned about combining AI-assisted development with engineering thinking in a project that had real architectural constraints.

## 1. The real job was not "writing code," it was reducing uncertainty

Looking back, the hardest part of the project was not implementation speed. It was uncertainty reduction.

At the beginning, there were many moving parts:

- JaamSim simulation output
- MQTT and digital twin state
- InfluxDB time-series data
- Kafka event flow
- PostgreSQL configuration and persistence
- an AI workflow expected to perform RCA instead of just chat
- a UI that needed to support both analysis and action approval

In that kind of environment, vibe coding was useful because it helped me turn vague ideas into working artifacts quickly. But the real progress did not come from generating code fast. It came from making the problem smaller and clearer, one layer at a time.

That changed how I thought about AI-assisted development. I stopped asking, "Can AI build this feature?" and started asking, "What uncertainty am I trying to eliminate in this iteration?"

Sometimes that uncertainty was technical:

- Can the watcher produce clean abnormal events?
- Can the consumer close the loop from Kafka to analysis to storage?
- Can the UI surface pending actions clearly enough to be trusted?

Sometimes it was conceptual:

- What is the actual source of truth for RCA?
- What counts as a root cause versus a symptom?
- Which part of the reasoning should be deterministic, and which part should be delegated to the model?

The most important lesson was that **vibe coding works best when it supports structured problem reduction, not when it replaces it.**

## 2. Architecture still matters more than generation

This project was not a single application. It was a layered system:

- a simulation layer
- a digital twin and event layer
- an AI reasoning layer
- a UI and operator-control layer

The first engineering instinct that helped was to separate responsibilities early.

The simulation layer should produce state.
The event layer should detect and forward abnormalities.
The workflow layer should gather facts and reason over them.
The UI should explain outcomes and gate actions.

Once those boundaries became clear, vibe coding became much more productive. It could help scaffold each layer, but it no longer had to invent the architecture while also writing the implementation.

This sounds obvious, but it became one of the main takeaways from the project: **AI can accelerate implementation inside a system design, but it is much less reliable when asked to invent the structure of a complex system from scratch.**

## 3. Defining sources of truth was more important than prompt design

Because this project focused on root cause analysis, it quickly became clear that "better prompting" was not the main bottleneck.

The real bottleneck was deciding what the workflow should trust.

I eventually treated the following as the key sources of truth:

- Ditto for device, vehicle, factory, and queue state
- InfluxDB for time-series evidence and alert generation
- PostgreSQL for topology, branch configuration, and persisted analysis
- local branch files for actual executable control values

That decision changed the entire workflow design.

Instead of asking the model to infer everything from an alert message, I moved toward a system where the workflow had to gather structured facts first. That made the difference between an answer that sounded intelligent and an answer that could actually be defended.

This was one of the clearest engineering lessons in the project: **before asking how the model should reason, first decide what the system is allowed to know.**

## 4. Good RCA depends on decomposition, not just intelligence

One of the biggest mistakes I made early on was treating RCA as if it were mainly a language problem.

It is not.

In practice, RCA worked only when I decomposed it into smaller questions:

- What happened?
- Which entity is affected?
- What facts are directly observable?
- What dependencies matter for this product or route?
- Is the issue local, upstream, downstream, or topological?
- Is the observed state a root cause or just a visible effect?

That decomposition shaped the workflow:

1. normalize the event
2. classify intent
3. collect facts
4. evaluate supply and transport constraints
5. summarize the RCA result
6. generate an actionable recommendation

This stepwise structure was not just a coding convenience. It was how I translated engineering reasoning into software behavior.

The key lesson was: **for RCA systems, workflow design is part of the reasoning model.**

## 5. The most important shift was from symptom-reading to causal reasoning

This project became much better once I stopped treating the most visible signal as the most meaningful one.

A stopped vehicle is not necessarily a vehicle problem.
An empty queue is not necessarily a production problem.
A blocked path is not necessarily the branch that should be changed.

In logistics systems, many alerts are downstream effects. The real cause may sit in transport routing, branch configuration, material dependencies, or the inability of carriers to return to the correct source path.

That changed how I approached RCA:

- check required inputs before explaining shortages
- inspect queue and inventory evidence before naming the cause
- relate shortages back to logistics edges and source factories
- ask whether the current branch state matches the topology implied by the configuration

This was less "AI magic" and more disciplined causal reasoning.

The lesson here is one I would carry into any future operations or industrial AI project: **the value of an RCA system comes from how well it separates root causes from symptoms, not from how fluent the final explanation sounds.**

## 6. Rules first, LLM second, was the right engineering trade-off

Over time, the workflow evolved into a hybrid model:

- deterministic tools and rules to identify facts and evaluate candidate causes
- an LLM to summarize the result, explain the causal chain, and suggest next checks

This turned out to be a much better trade-off than pure model-driven reasoning.

The deterministic part was good at:

- branch matching
- material dependency checks
- logistics-edge evaluation
- shortage classification
- candidate repair generation

The model was good at:

- writing a readable RCA summary
- organizing evidence
- describing confidence and next steps
- turning structured output into operator-friendly language

What I liked about this split is that it aligned with engineering priorities:

- accuracy before fluency
- debuggability before elegance
- explainability before autonomy

The lesson was clear: **in systems that may lead to real operational actions, the model should usually explain the reasoning, not be the only source of it.**

## 7. Human-in-the-loop was not a UX extra, it was part of the safety model

One of the defining design choices in this project was to treat branch modification as a controlled action rather than an automatic outcome.

That meant:

- the workflow could propose a branch change
- the UI would present the current and recommended values
- the operator would explicitly approve or reject the action

This was not only about caution. It was about making the system operationally credible.

Once an AI workflow moves from explanation to action, trust becomes a systems problem, not just a model problem. A human approval step becomes a way to:

- contain mistakes
- expose reasoning to review
- reduce the risk of silent bad changes
- preserve operator confidence

The broader lesson is that **engineering maturity in AI systems is often visible not in how much they automate, but in how carefully they control automation.**

## 8. Debuggability mattered more than polish

The digital twin part of the project taught me another engineering lesson.

At times the interface appeared frozen or laggy, but the actual causes were mixed:

- low-frequency upstream updates
- state-sync discontinuities
- iframe-related browser throttling
- interpolation settings that did not match the source cadence

At first, it was tempting to treat this as a rendering problem. But the more useful question was: what evidence do I need to distinguish a UI problem from a data problem?

That led to a better mindset:

- expose last-packet timestamps
- track packet counts
- surface connection state
- make the system observable before trying to make it beautiful

This was a reminder that **in engineering, diagnosability is often more valuable than visual sophistication.**

## 9. Vibe coding worked best when the project stayed iterable

The system did not emerge fully formed. It grew in loops:

- first get the event pipeline working
- then connect it to analysis
- then persist results
- then surface them in the UI
- then add branch proposals
- then add approval and control
- then improve explanation quality and observability

This iterative pattern mattered a lot.

Whenever the task was small and the goal was clear, vibe coding was effective. Whenever the task was too broad or underspecified, the results became noisy and hard to trust.

That reinforced a practical rule I now find very useful:

- define one clear objective per iteration
- keep the system runnable after each step
- optimize only after the loop is closed

The lesson is simple: **AI-assisted development becomes much stronger when the engineering process is incremental and testable.**

## 10. My final view of vibe coding

After this project, I do not see vibe coding as "letting AI build software for me."

I see it as a way to:

- explore implementation paths faster
- compress the distance from concept to prototype
- refactor and integrate more quickly
- externalize part of the trial-and-error process

But the core engineering responsibilities remain human:

- define the architecture
- establish the sources of truth
- decide safety boundaries
- interpret trade-offs
- judge whether the system is actually solving the right problem

That is probably the most honest conclusion I can draw from this project.

**Vibe coding is most valuable when it accelerates engineering judgment, not when it attempts to replace it.**
