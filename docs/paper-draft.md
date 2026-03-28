# Defense-in-Depth Against Prompt Injection in Search-Augmented AI Systems: A Three-Pillar Architecture

## Abstract

Large language models integrated with external data retrieval are vulnerable to indirect prompt injection — adversarial content in retrieved documents that hijacks the model's behavior. We present a three-pillar defense architecture implemented in a production search engine that serves as a retrieval backend for autonomous AI agents. The first pillar increases the structural distance between trusted instructions and untrusted retrieved content through input transformation and explicit provenance framing. The second pillar applies a multi-stage filtering pipeline — pattern matching, embedding-based classification, content transformation, and specialist model inspection — that removes attacks at successive layers with compounding effectiveness. The third and most critical pillar enforces an architectural separation between the observation component (which reads and processes external content) and the action component (which executes impactful operations), connected only by a narrow, lossy, structured handoff that strips imperative force from external content. We implement this architecture in a search system that ingests content from seven trusted sources, applies incremental filtering, and serves hybrid retrieval (BM25 + dense vector search) to LLM agents. Our approach differs from prior work in three ways: it is designed around a complete data pipeline (ingestion through serving) rather than a single-point defense; it treats the observation-action separation as the primary security boundary rather than a supplementary measure; and it operates in a confidential computing context where both prompt leakage and prompt injection must be prevented simultaneously. We argue that the combination of distance, filtering, and separation provides defense-in-depth that degrades gracefully — even when individual layers fail, the system's agency cannot be directly hijacked by external content.

## 1. Introduction

The integration of large language models (LLMs) with external data sources has created a new class of security vulnerability. When an LLM processes content retrieved from the web, documents, or databases, adversarial text embedded in that content can influence the model's behavior as if it were part of its own instructions. This indirect prompt injection [2] is fundamentally different from traditional security threats: it exploits the model's core capability (instruction following) rather than a software bug, and the attack surface is any content the system might retrieve.

The severity of the threat scales with the system's capabilities. A chatbot that can only generate text faces limited risk from injection — the worst outcome is misleading output. But an autonomous agent that can search the web, send emails, execute code, and manage files faces catastrophic risk: a single poisoned document can lead to data exfiltration, unauthorized actions, or cascading compromise across multi-agent workflows [6].

Existing defenses fall into three broad categories. **Input-level defenses** attempt to make the model better at distinguishing instructions from content — through training (instruction hierarchy [12], StruQ [13], SecAlign [14]), prompt engineering (spotlighting [17], sandwich defense [18]), or radical restriction of instruction-following capability (Jatmo [16]). **Detection-based defenses** apply classifiers, attention analysis, or embedding similarity to identify injections before they reach the model [9, 10, 22]. **Architectural defenses** restructure the system to limit what a compromised model can do, most notably through privilege separation between agents [15].

Each category has well-documented limitations. Input-level defenses are bypassed by creative adversaries: the HackAPrompt competition demonstrated that humans consistently find novel attack vectors against prompt-level defenses [3]. Detection-based defenses face a fundamental precision/recall trade-off and cannot catch subtle, distributed, or novel attacks. And architectural defenses, while powerful, have been studied primarily in isolation rather than as part of a complete data pipeline.

We argue that the right approach is **defense-in-depth** combining all three categories, and that this defense must be designed around the complete data lifecycle — from content ingestion through retrieval to LLM consumption. We present a three-pillar architecture implemented in a production search engine:

1. **Distance**: Structural and semantic separation between instructions and retrieved content, applied at every stage where external content enters the system.
2. **Filtering**: A multi-stage pipeline where each layer catches a fraction of attacks, compounding to high overall effectiveness even though no single layer is reliable.
3. **Separation**: A hard architectural boundary between the component that observes the world (processes external content) and the component that acts in it (executes impactful operations), connected by a narrow, structured, lossy handoff.

The third pillar is the primary security boundary. The first two pillars reduce the probability of successful injection; the third limits the damage when injection succeeds.

Our system additionally operates under a confidential computing constraint: query data must not leak outside the trusted execution environment. This creates a dual threat model where the system must defend against both inbound attacks (prompt injection through content) and outbound leakage (prompt or query exfiltration). The closed-corpus, trusted-source design of our search index addresses both simultaneously.

### Contributions

- A three-pillar defense architecture that integrates distance, filtering, and observation-action separation across a complete search-and-retrieval pipeline.
- An implementation in a production search engine serving seven trusted sources (Wikipedia, arXiv, German federal law, PubMed, RKI, Tagesschau, Deutsche Welle) with hybrid retrieval (BM25 + dense vector search via Qdrant).
- A concrete specification of the observation-action handoff — the structured, lossy interface between the reading and acting components — which we argue is the most critical and least studied element of prompt injection defense.
- Analysis of how confidential computing constraints interact with prompt injection defense, creating opportunities for architectural choices (closed corpus, trusted sources) that simultaneously address both threat models.

## 2. Related Work

### 2.1 Prompt Injection: Discovery and Taxonomy

Perez and Ribeiro [1] provided the first systematic study of prompt injection, identifying goal hijacking and prompt leaking as the two primary attack objectives. Greshake et al. [2] extended this to indirect prompt injection, where adversaries embed malicious instructions in external content (web pages, emails, documents) that LLM-integrated applications retrieve at inference time. Their work demonstrated novel attack vectors including data theft, agent worming, and information ecosystem contamination. Subsequent taxonomies [4, 21] have systematized the growing landscape of attacks along multiple dimensions: direct vs. indirect, optimization-free vs. optimization-based, and by attack objective.

The HackAPrompt competition [3] provided large-scale empirical evidence that prompt-level defenses are systematically vulnerable, generating a dataset of 600K+ adversarial prompts. The Tensor Trust game [19] produced 126K+ human-crafted attacks and 46K+ defenses, demonstrating that attack strategies discovered in adversarial games generalize to deployed applications.

### 2.2 Input-Level Defenses (Pillar 1)

The most principled input-level defense is OpenAI's Instruction Hierarchy [12], which trains models to prioritize system messages over user messages over third-party content. StruQ [13] goes further by creating a literal two-channel architecture where instructions and data flow through separate input paths, fine-tuning the model from a base (non-instruction-tuned) state to follow only the instruction channel. SecAlign [14] extends this with preference optimization, achieving less than 10% attack success even against unseen optimization-based attacks.

At the application level (without model modifications), Microsoft's Spotlighting [17] proposes encoding untrusted input with transformations such as base64, reducing attack success from over 50% to below 2%. The sandwich defense [18] reinforces the original instruction after external content, leveraging positional effects. These prompt-engineering approaches are weaker than training-based defenses but require no model access.

Jatmo [16] represents the radical end of the spectrum: fine-tuning task-specific models from base models that never learned general instruction-following. This eliminates the confusion between instructions and content entirely, achieving less than 0.5% attack success, but sacrifices flexibility.

**Relation to our work:** Our Pillar 1 operates at the application level (no model fine-tuning), applying transformation and framing at every point where external content enters the system. We combine spotlighting-style framing with structured representation (JSON with explicit provenance metadata), applied not just at the prompt level but throughout the data pipeline — at ingestion (content normalization), filtering (imperative language stripping), and serving (explicit trust framing in search results). This per-stage application is novel; prior work applies Pillar 1 defenses only at the prompt-model boundary.

### 2.3 Detection and Filtering (Pillar 2)

Detection approaches span multiple levels. At the input level, embedding-based classifiers [10] using Random Forest or XGBoost achieve moderate detection rates (AUC 0.764). Meta's PromptGuard 2 [22] provides a lightweight BERT-based classifier (86M parameters) for real-time detection. At the model-internal level, Attention Tracker [9] monitors attention patterns to detect the "distraction effect" of injection, improving detection by up to 10% AUROC. Google's defense of Gemini [11] combines continuous automated red teaming with iterative fine-tuning, explicitly acknowledging that model-level improvement is "a vital layer within a comprehensive defense-in-depth strategy."

The BIPIA benchmark [5] provides the first systematic evaluation framework for indirect prompt injection defenses, covering five application scenarios. Hidden-in-Plain-Text [7] extends this to social web content that survives real-world ingestion pipelines. The Agent Security Bench [20] provides the most comprehensive evaluation: 10 scenarios, 400+ tools, 27 attack/defense methods, with an 84.30% average attack success rate that demonstrates the inadequacy of current single-layer defenses.

**Relation to our work:** Our Pillar 2 applies detection at multiple points in the data pipeline, not just at the prompt boundary. We filter at ingestion time (pattern matching on all incoming content), at the filtering stage (statistical anomaly detection), and at query time (risk-aware ranking). This distributed filtering means that the same content is checked multiple times at different stages, by different methods, increasing the probability of catching attacks that any single check would miss.

### 2.4 Architectural Defenses (Pillar 3)

The strongest empirical result for architectural defense comes from OpenClaw [15], which implements privilege-separated agents: one agent processes untrusted content (planning agent) and another executes actions (action agent), with the planning agent having no tool access. This achieves 0% attack success on 649 attacks. Critically, their ablation shows agent isolation is the dominant mechanism (0.31% ASR alone) vs. input transformation alone (14.18% ASR), confirming that structural separation is more effective than any probabilistic defense.

Meta's LlamaFirewall [22] implements a production-grade three-component defense: PromptGuard 2 (input classifier), Agent Alignment Checks (LLM-based auditing of agent action chains), and CodeShield (static analysis). The Agent Alignment Checks represent a form of action-level separation — verifying that proposed actions align with user goals before execution.

AgentSentry [24] addresses temporal aspects: in multi-turn agent interactions, injection effects may be delayed. Their temporal causal diagnostics track how injected content influences reasoning over time, combined with context purification. Recent work on protocol-level vulnerabilities [25] extends the threat model to agent communication protocols (MCP, tool APIs), proposing cryptographic provenance tracking and sandboxed interfaces.

**Relation to our work:** Our Pillar 3 shares the observation-action separation principle with OpenClaw [15] but differs in two key ways. First, we integrate it with the data pipeline rather than treating it as a standalone agent pattern: the search engine itself is part of the observation layer, and content is preprocessed and structured before reaching any agent. Second, we specify the handoff interface in detail — a lossy, structured schema that translates external content from imperative language into factual claims about the world. This handoff design is, to our knowledge, not specified in prior work, which typically describes the separation principle without detailing how information crosses the boundary safely.

### 2.5 RAG-Specific Threats

Indirect prompt injection through RAG pipelines presents unique challenges. Backdoored retrievers [8] demonstrate that the retrieval mechanism itself can be compromised to preferentially surface poisoned documents, bypassing content-level defenses entirely. The "retrieval barrier" work [6] shows that attackers can decompose injections into trigger and attack fragments that are individually benign but devastating when co-retrieved.

**Relation to our work:** Our closed-corpus, trusted-source approach addresses RAG-specific threats differently from open-web retrieval systems. By ingesting only from vetted sources (Wikipedia, government sites, established academic publishers) and controlling the entire pipeline from download to indexing, we eliminate the retrieval poisoning attack vector. The confidential computing design further ensures that no external party can influence what enters the corpus at runtime. This is a stronger guarantee than any detection-based defense but comes at the cost of corpus coverage.

### 2.6 Summary: Positioning Our Contribution

The literature reveals a clear convergence toward defense-in-depth: the most effective systems (Gemini [11], LlamaFirewall [22], OpenClaw [15]) combine multiple defense types. However, existing work has three gaps that we address:

1. **Pipeline-integrated defense.** Most defenses are designed as point solutions (at the prompt boundary, at the classifier stage, or at the action gate). Our architecture distributes defense across the entire data lifecycle: ingestion, filtering, indexing, retrieval, and agent interaction.

2. **Handoff specification.** The observation-action separation is described in principle (OpenClaw [15], LlamaFirewall Agent Alignment [22]) but the interface between the observation and action components — what information crosses, how it is structured, what is deliberately lost — is not specified. We provide a concrete schema design.

3. **Dual threat model.** Existing work focuses on inbound threats (prompt injection). Our confidential computing context adds outbound threats (prompt/query leakage), and our architecture addresses both simultaneously through closed-corpus design and local execution.

## References

[1] Perez, F. & Ribeiro, I. (2022). "Ignore Previous Prompt: Attack Techniques For Language Models." arXiv:2211.09527.

[2] Greshake, K., Abdelnabi, S., Mishra, S., Endres, C., Holz, T., & Fritz, M. (2023). "Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection." AISec '23 / arXiv:2302.12173.

[3] Schulhoff, S., Pinto, J., Khan, A., et al. (2023). "Ignore This Title and HackAPrompt: Exposing Systemic Vulnerabilities of LLMs Through a Global Scale Prompt Hacking Competition." EMNLP 2023 / arXiv:2311.16119.

[4] Hou, L., et al. (2024). "Prompt Injection Attacks on Large Language Models: A Survey." ScienceDirect, 2024.

[5] Yi, J., Xie, Y., Zhu, B., et al. (2023). "Benchmarking and Defending Against Indirect Prompt Injection Attacks on Large Language Models." KDD 2025 / arXiv:2312.14197.

[6] "Overcoming the Retrieval Barrier: Indirect Prompt Injection in the Wild for LLM Systems." arXiv:2601.07072, 2026.

[7] "Hidden-in-Plain-Text: A Benchmark for Social-Web Indirect Prompt Injection in RAG." arXiv:2601.10923, 2026.

[8] "Backdoored Retrievers for Prompt Injection Attacks on Retrieval Augmented Generation of Large Language Models." arXiv:2410.14479, 2024.

[9] Huo, K., et al. (2024). "Attention Tracker: Detecting Prompt Injection Attacks in LLMs." Findings of NAACL 2025 / arXiv:2411.00348.

[10] "Embedding-Based Classifiers Can Detect Prompt Injection Attacks." CEUR-WS Vol-3920 / arXiv:2410.22284, 2024.

[11] Google DeepMind (2025). "Lessons from Defending Gemini Against Indirect Prompt Injections." arXiv:2505.14534.

[12] Wallace, E., Xiao, K., Leber, R., Kosseim, L., & Steinhardt, J. (2024). "The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions." OpenAI / arXiv:2404.13208.

[13] Chen, S., Piet, J., Sitawarin, C., & Wagner, D. (2024). "StruQ: Defending Against Prompt Injection with Structured Queries." USENIX Security 2025 / arXiv:2402.06363.

[14] Chen, S., Zharmagambetov, A., Mahloujifar, S., et al. (2024). "SecAlign: Defending Against Prompt Injection with Preference Optimization." ACM CCS 2025 / arXiv:2410.05451.

[15] "Agent Privilege Separation in OpenClaw: A Structural Defense Against Prompt Injection." arXiv:2603.13424, 2026.

[16] Piet, J., Alrashed, M., Sitawarin, C., et al. (2023). "Jatmo: Prompt Injection Defense by Task-Specific Finetuning." ESORICS 2024 / arXiv:2312.17673.

[17] Hines, K., Lopez, G., Hall, M., et al. (2024). "Defending Against Indirect Prompt Injection Attacks With Spotlighting." CEUR-WS Vol-3920 / arXiv:2403.14720.

[18] "Defense Against Prompt Injection Attack by Leveraging Attack Techniques." ACL 2025 / arXiv:2411.00459, 2024.

[19] Toyer, S., Watkins, O., Mendes, E. A., et al. (2023). "Tensor Trust: Interpretable Prompt Injection Attacks from an Online Game." ICLR 2024 / arXiv:2311.01011.

[20] Zhang, H., et al. (2024). "Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents." ICLR 2025 / arXiv:2410.02644.

[21] "An Early Categorization of Prompt Injection Attacks on Large Language Models." arXiv:2402.00898, 2024.

[22] Cheng, Y., et al. (2025). "LlamaFirewall: An Open Source Guardrail System for Building Secure AI Agents." Meta / arXiv:2505.03574.

[23] "Securing AI Agents Against Prompt Injection Attacks: A Comprehensive Benchmark and Defense Framework." arXiv:2511.15759, 2025.

[24] "AgentSentry: Mitigating Indirect Prompt Injection in LLM Agents via Temporal Causal Diagnostics and Context Purification." arXiv:2602.22724, 2026.

[25] "From Prompt Injections to Protocol Exploits: Threats in LLM-Powered AI Agents Workflows." arXiv:2506.23260, 2025.
