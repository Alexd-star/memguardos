# Memory in the Age of AI Agents: A Survey

## Forms, Functions and Dynamics

Yuyang Hu<sup>†</sup>, Shichun Liu<sup>†</sup>, Yanwei Yue<sup>†</sup>, Guibin Zhang<sup>†✉</sup>, Boyang Liu, Fangyi Zhu, Jiahang Lin, Honglin Guo, Shihan Dou, Zhiheng Xi, Senjie Jin, Jiejun Tan, Yanbin Yin, Jiongnan Liu, Zeyu Zhang, Zhongxiang Sun, Yutao Zhu, Hao Sun, Boci Peng, Zhenrong Cheng, Xuanbo Fan, Jiaxin Guo, Xinlei Yu, Zhenhong Zhou, Zewen Hu, Jiahao Huo, Junhao Wang, Yuwei Niu, Yu Wang, Zhenfei Yin, Xiaobin Hu, Yue Liao, Qiankun Li, Kun Wang, Wangchunshu Zhou, Yixin Liu, Dawei Cheng, Qi Zhang, Tao Gui<sup>‡</sup>, Shirui Pan, Yan Zhang<sup>‡</sup>, Philip Torr, Zhicheng Dou<sup>‡</sup>, Ji-Rong Wen, Xuanjing Huang<sup>‡</sup>, Yu-Gang Jiang, Shuicheng Yan<sup>‡</sup>

<sup>†</sup>Core Contributors with Names Listed Alphabetically. Project Organizer. <sup>‡</sup>Core Supervisors.

**Affiliations:** National University of Singapore, Renmin University of China, Fudan University, Peking University, Nanyang Technological University, Tongji University, University of California San Diego, Hong Kong University of Science and Technology (Guangzhou), Griffith University, Georgia Institute of Technology, OPPO, Oxford University

Memory has emerged, and will continue to remain, a core capability of foundation model-based agents. It underpins long-horizon reasoning, continual adaptation, and effective interaction with complex environments. As research on agent memory rapidly expands and attracts unprecedented attention, the field has also become increasingly fragmented. Existing works that fall under the umbrella of agent memory often differ substantially in their motivations, implementations, assumptions, and evaluation protocols, while the proliferation of loosely defined memory terminologies has further obscured conceptual clarity. Traditional taxonomies such as long/short-term memory have proven insufficient to capture the diversity and dynamics of contemporary agent memory systems. This survey aims to provide an up-to-date and comprehensive landscape of current agent memory research. We begin by clearly delineating the scope of agent memory and distinguishing it from related concepts such as LLM memory, retrieval augmented generation (RAG), and context engineering. We then examine agent memory through the unified lenses of **forms**, **functions**, and **dynamics**. From the perspective of forms, we identify three dominant realizations of agent memory, namely *token-level*, *parametric*, and *latent memory*. From the perspective of functions, we move beyond coarse temporal categorizations and propose a finer-grained taxonomy that distinguishes *factual*, *experiential*, and *working memory*. From the perspective of dynamics, we analyze how memory is formed, evolved, and retrieved over time as agents interact with their environments. To support empirical research and practical development, we compile a comprehensive summary of representative benchmarks and open source memory frameworks. Beyond consolidation, we articulate a forward-looking perspective on emerging research frontiers, including automation-oriented memory design, the deep integration of reinforcement learning with memory systems, multimodal memory, shared memory for multi-agent systems, and trustworthiness issues. We hope this survey serves not only as a reference for existing work, but also as a conceptual foundation for rethinking memory as a first-class primitive in the design of future agentic intelligence.

✉ **Main Contact:** guibinz@u.nus.edu, yuyang.hu@ruc.edu.cn, liusc24@m.fudan.edu.cn, ywyue25@stu.pku.edu.cn

**Github:** <https://github.com/Shichun-Liu/Agent-Memory-Paper-List>

---

Note: If you identify your own or other papers relevant to this survey that have not been discussed (we apologize for any such omissions due to the rapidly expanding literature), please feel free to contact us via email or raise an issue on [GitHub](#).# Contents

<table><tr><td><b>1</b></td><td><b>Introduction</b></td><td><b>4</b></td></tr><tr><td><b>2</b></td><td><b>Preliminaries: Formalizing Agents and Memory</b></td><td><b>6</b></td></tr><tr><td>2.1</td><td>LLM-based Agent Systems</td><td>6</td></tr><tr><td>2.2</td><td>Agent Memory Systems</td><td>7</td></tr><tr><td>2.3</td><td>Comparing Agent Memory with Other Key Concepts</td><td>8</td></tr><tr><td>2.3.1</td><td>Agent Memory vs. LLM Memory</td><td>9</td></tr><tr><td>2.3.2</td><td>Agent Memory vs. RAG</td><td>10</td></tr><tr><td>2.3.3</td><td>Agent Memory vs. Context Engineering</td><td>11</td></tr><tr><td><b>3</b></td><td><b>Form: What Carries Memory?</b></td><td><b>12</b></td></tr><tr><td>3.1</td><td>Token-level Memory</td><td>13</td></tr><tr><td>3.1.1</td><td>Flat Memory (1D)</td><td>15</td></tr><tr><td>3.1.2</td><td>Planar Memory (2D)</td><td>20</td></tr><tr><td>3.1.3</td><td>Hierarchical Memory (3D)</td><td>21</td></tr><tr><td>3.2</td><td>Parametric Memory</td><td>22</td></tr><tr><td>3.2.1</td><td>Internal Parametric Memory</td><td>22</td></tr><tr><td>3.2.2</td><td>External Parametric Memory</td><td>24</td></tr><tr><td>3.3</td><td>Latent Memory</td><td>26</td></tr><tr><td>3.3.1</td><td>Generate</td><td>26</td></tr><tr><td>3.3.2</td><td>Reuse</td><td>28</td></tr><tr><td>3.3.3</td><td>Transform</td><td>28</td></tr><tr><td>3.4</td><td>Adaptation</td><td>30</td></tr><tr><td><b>4</b></td><td><b>Functions: Why Agents Need Memory?</b></td><td><b>31</b></td></tr><tr><td>4.1</td><td>Factual Memory</td><td>32</td></tr><tr><td>4.1.1</td><td>User factual memory</td><td>35</td></tr><tr><td>4.1.2</td><td>Environment factual memory</td><td>36</td></tr><tr><td>4.2</td><td>Experiential Memory</td><td>37</td></tr><tr><td>4.2.1</td><td>Case-based Memory</td><td>39</td></tr><tr><td>4.2.2</td><td>Strategy-based Memory</td><td>40</td></tr><tr><td>4.2.3</td><td>Skill-based Memory</td><td>41</td></tr><tr><td>4.2.4</td><td>Hybrid memory</td><td>42</td></tr><tr><td>4.3</td><td>Working Memory</td><td>42</td></tr><tr><td>4.3.1</td><td>Single-turn Working Memory</td><td>43</td></tr><tr><td>4.3.2</td><td>Multi-turn Working Memory</td><td>45</td></tr><tr><td><b>5</b></td><td><b>Dynamics: How Memory Operates and Evolves?</b></td><td><b>46</b></td></tr><tr><td>5.1</td><td>Memory Formation</td><td>48</td></tr><tr><td>5.1.1</td><td>Semantic Summarization</td><td>48</td></tr><tr><td>5.1.2</td><td>Knowledge Distillation</td><td>50</td></tr><tr><td>5.1.3</td><td>Structured Construction</td><td>51</td></tr><tr><td>5.1.4</td><td>Latent Representation</td><td>53</td></tr><tr><td>5.1.5</td><td>Parametric Internalization</td><td>54</td></tr><tr><td>5.2</td><td>Memory Evolution</td><td>55</td></tr><tr><td>5.2.1</td><td>Consolidation</td><td>55</td></tr><tr><td>5.2.2</td><td>Updating</td><td>57</td></tr><tr><td>5.2.3</td><td>Forgetting</td><td>58</td></tr><tr><td>5.3</td><td>Memory Retrieval</td><td>59</td></tr><tr><td>5.3.1</td><td>Retrieval Timing and Intent</td><td>60</td></tr><tr><td>5.3.2</td><td>Query Construction</td><td>62</td></tr><tr><td>5.3.3</td><td>Retrieval Strategies</td><td>62</td></tr><tr><td>5.3.4</td><td>Post-Retrieval Processing</td><td>64</td></tr></table><table>
<tr>
<td><b>6</b></td>
<td><b>Resources and Frameworks</b></td>
<td><b>65</b></td>
</tr>
<tr>
<td>6.1</td>
<td>Benchmarks and Datasets . . . . .</td>
<td>65</td>
</tr>
<tr>
<td>6.1.1</td>
<td>Benchmarks for Memory / Lifelong / Self-Evolving Agents . . . . .</td>
<td>65</td>
</tr>
<tr>
<td>6.1.2</td>
<td>Other Related Benchmarks . . . . .</td>
<td>67</td>
</tr>
<tr>
<td>6.2</td>
<td>Open-Source Frameworks . . . . .</td>
<td>68</td>
</tr>
<tr>
<td><b>7</b></td>
<td><b>Positions and Frontiers</b></td>
<td><b>69</b></td>
</tr>
<tr>
<td>7.1</td>
<td>Memory Retrieval vs. Memory Generation . . . . .</td>
<td>69</td>
</tr>
<tr>
<td>7.1.1</td>
<td>Look Back: From Memory Retrieval to Memory Generation . . . . .</td>
<td>69</td>
</tr>
<tr>
<td>7.1.2</td>
<td>Future Perspective . . . . .</td>
<td>69</td>
</tr>
<tr>
<td>7.2</td>
<td>Automated Memory Management . . . . .</td>
<td>70</td>
</tr>
<tr>
<td>7.2.1</td>
<td>Look-Back: From Hand-crafted to Automatically Constructed Memory Systems. . . . .</td>
<td>70</td>
</tr>
<tr>
<td>7.2.2</td>
<td>Future Perspective . . . . .</td>
<td>70</td>
</tr>
<tr>
<td>7.3</td>
<td>Reinforcement Learning Meets Agent Memory . . . . .</td>
<td>71</td>
</tr>
<tr>
<td>7.3.1</td>
<td>Look-Back: RL is Internalizing Memory Management Abilities for Agents. . . . .</td>
<td>71</td>
</tr>
<tr>
<td>7.3.2</td>
<td>Future Perspective . . . . .</td>
<td>72</td>
</tr>
<tr>
<td>7.4</td>
<td>Multimodal Memory . . . . .</td>
<td>72</td>
</tr>
<tr>
<td>7.4.1</td>
<td>Look-Back . . . . .</td>
<td>72</td>
</tr>
<tr>
<td>7.4.2</td>
<td>Future Perspective . . . . .</td>
<td>73</td>
</tr>
<tr>
<td>7.5</td>
<td>Shared Memory in Multi-Agent Systems . . . . .</td>
<td>73</td>
</tr>
<tr>
<td>7.5.1</td>
<td>Look-Back: From Isolated Memories to Shared Cognitive Substrates . . . . .</td>
<td>73</td>
</tr>
<tr>
<td>7.5.2</td>
<td>Future Perspective . . . . .</td>
<td>73</td>
</tr>
<tr>
<td>7.6</td>
<td>Memory for World Model . . . . .</td>
<td>74</td>
</tr>
<tr>
<td>7.6.1</td>
<td>Look-Back . . . . .</td>
<td>74</td>
</tr>
<tr>
<td>7.6.2</td>
<td>Future Perspective . . . . .</td>
<td>74</td>
</tr>
<tr>
<td>7.7</td>
<td>Trustworthy Memory . . . . .</td>
<td>75</td>
</tr>
<tr>
<td>7.7.1</td>
<td>Look-Back: From Trustworthy RAG to Trustworthy Memory . . . . .</td>
<td>75</td>
</tr>
<tr>
<td>7.7.2</td>
<td>Future Perspective . . . . .</td>
<td>75</td>
</tr>
<tr>
<td>7.8</td>
<td>Human-Cognitive Connections . . . . .</td>
<td>76</td>
</tr>
<tr>
<td>7.8.1</td>
<td>Look Back . . . . .</td>
<td>76</td>
</tr>
<tr>
<td>7.8.2</td>
<td>Future Perspective . . . . .</td>
<td>76</td>
</tr>
<tr>
<td><b>8</b></td>
<td><b>Conclusion</b></td>
<td><b>76</b></td>
</tr>
</table>The diagram illustrates a unified taxonomy of agent memory artifacts. It is organized into three main dimensions: **Memory Form** (Token-level, Parametric, Latent), **Function** (Vector Database, Model & Knowledge Editing, Latent Repository), and **Dynamics** (Short-term, Long-term). The artifacts are grouped into several functional categories:

- **Context Condensation:** MemAgent, MemSearcher, ReSum, MEM1, Context-lite RL, RecurrentGPT, IterResearch.
- **Context Branching:** ContextFolding, AgentFold, HiAgent.
- **Internalizing Experiences:** AgentBank, Agent-PLAN, Agent-Lumos, Agent-Trek, ETO, Meta Experience, Agent-R1, Chain-of-Agent, RAGEN + StarPO, Steca.
- **KV Generation:** Titans, LM2, MemoRAG, Co-Processor.
- **KV Reuse/Compression:** AutoCompressor, DMS, SnapKV, PyramidKV, H2O, Gist, KVPRESS, Cluster Attn.
- **Latent Memory Generation:** SoftCoT, SoftCoT++, MemGen, VisMem, LatentSeek, CoMEM, Continuous GUI Memory.
- **Extract Insights:** ExpeL, G-Memory, UFO2, Agent KB, Buffer-of-Thought, DGM, Dynamic Cheatsheet, Mem^p, H2R, ToolMem, ChemAgent, Voyager, LEGOMem, JARVIS-1.
- **Multimodal RAG:** M3-Agent, Ella, Ego-LLaVA, KAIST WorldMM, MemoRo, MIRIX, VideoAgent, Mem2Ego.
- **Knowledge Graph:** Trainable Graph Memory, Memo^g, AriGraph, cognee, Zep AI, SGMem, A-MEM.
- **Model & Knowledge Editing:** AlphaEdit, ROME, MEMIT, SERAC, Memory Decoder, MLP Memory.

A legend on the right defines the color coding: **Divided by Memory Form** (light blue for Token-level, medium blue for Parametric, dark blue for Latent) and **Divided by Function** (light orange for Vector Database, medium orange for Model & Knowledge Editing, dark orange for Latent Repository). The diagram also shows the relationship between memory forms and dynamics, with a **Latent Repository** containing MemoryLLM M+, MemoryVLA, SirLLM Memory^3, and TrackVLA++.

**Figure 1** Overview of agent memory organized by the unified taxonomy of *forms* (Section 3), *functions* (Section 4), and *dynamics* (Section 5). The diagram positions memory artifacts by their dominant form and primary function. It further maps representative systems into this taxonomy to provide a consolidated landscape.

## 1 Introduction

The past two years have witnessed the overwhelming evolution of increasingly capable large language models (LLMs) into powerful AI agents (Matarazzo and Torlone, 2025; Minaee et al., 2025; Luo et al., 2025a). These foundation-model-powered agents have demonstrated remarkable progress across diverse domains such as deep research (Xu and Peng, 2025; Zhang et al., 2025p), software engineering (Wang et al., 2024i), and scientific discovery (Wei et al., 2025c), continuously advancing the trajectory toward artificial general intelligence (AGI) (Fang et al., 2025a; Durante et al., 2024). Although early conceptions of “agents” were highly heterogeneous, a growing consensus has since emerged within the community: beyond a pure LLM backbone, an agent is typically equipped with capabilities such as *reasoning*, *planning*, *perception*, *memory*, and *tool-use*. Some of these abilities, such as reasoning and tool-use, have been largely internalized within model parameters through reinforcement learning (Wang et al., 2025m; Qu et al., 2025b), while some still depend heavily on external agentic scaffolds. Together, these components transform LLMs from static conditional generators into learnable policies that can interact with diverse external environments and adaptively evolve over time (Zhang et al., 2025f; Liu et al., 2025a).

Among these agentic faculties, *memory* stands out as a cornerstone, explicitly enabling the transformation of static LLMs, whose parameters cannot be rapidly updated, into adaptive agents capable of continual adaptation through environmental interaction (Zhang et al., 2025s; Wu et al., 2025g). From an application perspective, numerous domains demand agents with proactive memory management rather than ephemeral, forgetful behaviors: personalized chatbots (Chhikara et al., 2025; Li et al., 2025b), recommender systems (Liu et al., 2025c), social simulations (Park et al., 2023; Yang et al., 2025), and financial investigations (Zhang et al., 2024) all rely on the agent’s ability to process, store, and manage historical information. From a developmental standpoint, one of the defining aspirations of AGI research is to endow agents with the capacity for continual evolution through environment interactions (Hendrycks et al., 2025), a capability fundamentallygrounded in agent memory.

**Agent Memory Needs A New Taxonomy** Given the growing significance and community attention surrounding agent memory systems, it has become both timely and necessary to provide an updated perspective on contemporary agent memory research. The motivation for a new taxonomy and survey is twofold: **① Limitations of Existing Taxonomies:** While several recent surveys have provided valuable and comprehensive overviews of agent memory (Zhang et al., 2025s; Wu et al., 2025g), their taxonomies were developed prior to a number of rapid methodological advances and therefore do not fully reflect the current breadth and complexity of the research landscape. For example, emerging directions in 2025, such as memory frameworks that distill reusable tools from past experiences (Qiu et al., 2025a,c; Zhao et al., 2025c), or memory-augmented test-time scaling methods (Zhang et al., 2025g; Suzgun et al., 2025), remain underrepresented in earlier classification schemes. **② Conceptual Fragmentation:** With the explosive growth of memory-related studies, the concept itself has become increasingly expansive and fragmented. Researchers often find that papers claiming to study “agent memory” differ drastically in implementation, objectives, and underlying assumptions. The proliferation of diverse terminologies (declarative, episodic, semantic, parametric memory, etc.) further obscures conceptual clarity, highlighting the urgent need for a coherent taxonomy that can unify these emerging concepts.

Therefore, this paper seeks to establish a systematic framework that reconciles existing definitions, bridges emerging trends, and elucidates the foundational principles of memory in agentic systems. Specifically, this survey aims to address the following key questions:

### Key Questions

- ① How is *agent memory* defined, and how does it relate to related concepts such as LLM memory, retrieval-augmented generation (RAG), and context engineering?
- ② **Forms:** What architectural or representational forms can agent memory take?
- ③ **Functions:** Why is agent memory needed, and what roles or purposes does it serve?
- ④ **Dynamics:** How does agent memory operate, adapt, and evolve over time?
- ⑤ What are the promising frontiers for advancing agent memory research?

To address question ①, we first provide formal definitions for LLM-based agents and agent memory systems in Section 2, and present a detailed comparison between agent memory and related concepts such as LLM memory, RAG, and context engineering. Following the “Forms–Functions–Dynamics” triangle, we offer a structured overview of agent memory. Question ② examines the architectural forms of memory, which we discuss in Section 3, highlighting three mainstream implementations: token-level, parametric, and latent memory. Question ③ concerns the functional roles of memory, addressed in Section 4, where we distinguish between *factual memory*, which records knowledge from agents’ interactions with users and the environment; *experiential memory*, which incrementally enhances the agent’s problem-solving capabilities through task execution; and *working memory*, which manages workspace information during individual task instances. Question ④ focuses on the lifecycle and operational dynamics of agent memory, which we present sequentially in terms of memory formulation, retrieval, and evolution.

After surveying existing research through the lenses of “Forms–Functions–Dynamics,” we further provide our perspectives and insights on agent memory research. To facilitate knowledge sharing and future development, we first summarize key benchmarks and framework resources in Section 6. Building upon this foundation, we then address question ⑤ by exploring several emerging yet underdeveloped research frontiers in Section 7, including automation-oriented memory design, the integration of reinforcement learning (RL), multimodal memory, shared memory for multi-agent systems, and trustworthy issues.

**Contributions** The contributions of this survey can be summarized as follows: (1) We present an up-to-date and multidimensional taxonomy of agent memory from the perspective of “forms–functions–dynamics,” offering a structured lens through which to understand current developments in the field. (2) We provide an in-depth discussion on the suitability and interplay of different memory forms and functional purposes,offering insights into how various memory types can be effectively aligned with distinct agentic objectives. (3) We investigate emerging and promising research directions in agent memory, thereby outlining future opportunities and guiding pathways for advancement. (4) We compile a comprehensive collection of resources, including benchmarks and open-source frameworks, to support both researchers and practitioners in further exploration of agent memory systems.

**Outline of the Survey** The remainder of this survey is organized as follows. Section 2 formalizes LLM-based agents and agent memory systems, and clarifies their relationships with related concepts. Section 3, Section 4, and Section 5 respectively examine the forms, functions, and dynamics of agent memory. Section 6 summarizes representative benchmarks and framework resources. Section 7 discusses emerging research frontiers and future directions. Finally, we conclude the survey with a summary of key insights in Section 8.

## 2 Preliminaries: Formalizing Agents and Memory

LLM agents increasingly serve as the decision-making core of interactive systems that operate over time, manipulate external tools, and coordinate with humans or other agents. To study memory in such settings, we begin by formalizing LLM-based agent systems in a manner that encompasses both single-agent and multi-agent configurations. We then formalize the memory system coupled to the agent’s decision process through read/write interactions, enabling a unified treatment of memory phenomena that arise both *within* a task (inside-trial / short-term memory) and *across* tasks (cross-trial / long-term memory).

### 2.1 LLM-based Agent Systems

**Agents and Environment** Let  $\mathcal{I} = \{1, \dots, N\}$  denote the index set of agents, where  $N = 1$  corresponds to the single-agent case (e.g., ReAct), and  $N > 1$  represents multi-agent settings such as debate (Li et al., 2024c) or planner–executor architectures (Wan et al., 2025). The environment is characterized by a state space  $\mathcal{S}$ . At each time step  $t$ , the environment evolves according to a controlled stochastic transition model

$$s_{t+1} \sim \Psi(s_{t+1} \mid s_t, a_t),$$

where  $a_t$  denotes the action executed at time  $t$ . In multi-agent systems, this abstraction allows for either sequential decision-making (where a single agent acts at each step) or implicit coordination through environment-mediated effects. Each agent  $i \in \mathcal{I}$  receives an observation

$$o_t^i = O_i(s_t, h_t^i, \mathcal{Q}),$$

where  $h_t^i$  denotes the portion of the interaction history visible to agent  $i$ . This history may include previous messages, intermediate tool outputs, partial reasoning traces, shared workspace states, or other agents’ contributions, depending on the system design.  $\mathcal{Q}$  denotes the task specification, such as a user instruction, goal description, or external constraints, which is treated as fixed within a task unless otherwise specified.

**Action Space** A distinguishing feature of LLM-based agents is the heterogeneity of their action space. Rather than restricting actions to plain text generation, agents may operate over a multimodal and semantically structured action space, including:

- • **Natural-language generation**, such as producing intermediate reasoning, explanations, responses, or instructions (Li et al., 2023b; Wu et al., 2024b; Hong et al., 2024; Qian et al., 2024).
- • **Tool invocation actions**, which call external APIs, search engines, calculators, databases, simulators, or code execution environments (Qin et al., 2025; Li et al., 2025h; Zhou et al., 2023c, 2024c).
- • **Planning actions**, which explicitly output task decompositions, execution plans, or subgoal specifications to guide later behavior (CAMEL-AI, 2025; Liu et al., 2025g; Pan et al., 2024).
- • **Environment-control actions**, where the agent directly manipulates the external environment (e.g., navigation in embodied settings (Shridhar et al., 2021; Wang et al., 2022a), editing a software repository (Jimenez et al., 2024; Aleithan et al., 2024), or modifying a shared memory buffer).- • **Communication actions**, enabling collaboration or negotiation with other agents through structured messages (Marro et al., 2024).

These actions, though diverse in semantics, are unified by the fact that they are produced through an autoregressive LLM backbone conditioned on a contextual input. Formally, each agent  $i$  follows a policy

$$a_t = \pi_i(o_t^i, m_t^i, \mathcal{Q}),$$

where  $m_t^i$  is a memory-derived signal defined in Section 2.2. The policy may internally generate multi-step reasoning chains, latent deliberation, or scratchpad computations prior to emitting an executable action; such internal processes are abstracted away and not explicitly modeled.

**Interaction Process and Trajectories** A full execution of the system induces a trajectory

$$\tau = (s_0, o_0, a_0, s_1, o_1, a_1, \dots, s_T),$$

where  $T$  is determined by task termination conditions or system-specific stopping criteria. At each step, the trajectory reflects the interleaving of (i) environment observation, (ii) optional memory retrieval, (iii) LLM-based computation, and (iv) action execution that drives the next state transition.

This formulation captures a broad class of agentic systems, ranging from a single agent solving reasoning tasks with tool augmentation to teams of role-specialized agents collaboratively developing software (Qian et al., 2024; Wang et al., 2025l) or conducting scientific inquiry (Weng et al., 2025). We next formalize the memory systems that integrate into this agent loop.

## 2.2 Agent Memory Systems

While an LLM-based agent interacts with an environment, its instantaneous observation  $o_t^i$  is often insufficient for effective decision-making. Agents therefore rely on additional information derived from prior interactions, both within the current task and across previously completed tasks. We formalize this capability through a unified *agent memory system*, represented as an evolving memory state

$$\mathcal{M}_t \in \mathbb{M},$$

where  $\mathbb{M}$  denotes the space of admissible memory configurations. No specific internal structure is imposed on  $\mathcal{M}_t$ ; it may take the form of a text buffer, key–value store, vector database, graph structure, or any hybrid representation. At the beginning of a task,  $\mathcal{M}_t$  may already contain information distilled from prior trajectories (cross-trial memory). During task execution, new information accumulates and functions as short-term, task-specific memory. Both roles are supported within a single memory container, with temporal distinctions emerging from usage patterns rather than architectural separation.

**Memory Lifecycle: Formation, Evolution, and Retrieval** The dynamics of the memory system are characterized by three conceptual operators.

**Memory Formation** At time step  $t$ , the agent produces informational artifacts  $\phi_t$ , which may include tool outputs, reasoning traces, partial plans, self-evaluations, or environmental feedback. A formation operator

$$\mathcal{M}_{t+1}^{\text{form}} = F(\mathcal{M}_t, \phi_t)$$

selectively transforms these artifacts into memory candidates, extracting information with potential future utility rather than storing the entire interaction history verbatim.

**Memory Evolution** Formed memory candidates are integrated into the existing memory base through an evolution operator

$$\mathcal{M}_{t+1} = E(\mathcal{M}_{t+1}^{\text{form}}),$$

which may consolidate redundant entries (Zhao et al., 2024), resolve conflicts (Rasmussen et al., 2025; Li et al., 2025l), discard low-utility information (Wang et al., 2025r), or restructure memory for efficient retrieval. The resulting memory state persists across subsequent decision steps and tasks.**Memory Retrieval** When selecting an action, agent  $i$  retrieves a context-dependent memory signal

$$m_t^i = R(\mathcal{M}_t, o_t^i, \mathcal{Q}),$$

where  $R$  denotes a retrieval operator that constructs a task-aware query and returns relevant memory content. The retrieved signal  $m_t^i$  is formatted for direct consumption by the LLM policy, for example as a sequence of textual snippets or a structured summary.

**Temporal Roles Within the Agent Loop** Although memory is represented as a unified state  $\mathcal{M}_t$ , the three lifecycle operators (formation  $F$ , evolution  $E$ , and retrieval  $R$ ) need not be invoked at every time step. Instead, different memory effects arise from distinct temporal invocation patterns. For instance, some systems perform retrieval only once at task initialization,

$$m_t^i = \begin{cases} R(\mathcal{M}_0, o_0^i, \mathcal{Q}), & t = 0, \\ \perp, & t > 0, \end{cases}$$

where  $\perp$  denotes null retrieval strategy. Others may retrieve memory intermittently or continuously based on contextual triggers. Similarly, memory formation may range from minimal accumulation of raw observations,

$$\mathcal{M}_{t+1}^{\text{form}} = \mathcal{M}_t \cup \{o_t^i\},$$

to sophisticated extraction and refinement of reusable patterns or abstractions. Thus, *inside a task*, short-term memory effects may arise from lightweight logging just as in [Yao et al. \(2023b\)](#); [Chen et al. \(2023a\)](#) or from more elaborate iterative refinement ([Hu et al., 2025a](#)); *across tasks*, long-term memory may be updated episodically at task boundaries or continuously throughout operation. Short-term and long-term memory phenomena therefore emerge not from discrete architectural modules but from the temporal patterns with which formation, evolution, and retrieval are engaged.

**Memory–Agent Coupling** The interaction between memory and the agent’s decision process is similarly flexible. In general, the agent policy is written as

$$a_t = \pi_i(o_t^i, m_t^i, \mathcal{Q}),$$

where the retrieved memory signal  $m_t^i$  may be present or absent depending on the retrieval schedule. When retrieval is disabled at a given step,  $m_t^i$  can be treated as a distinguished null input.

Consequently, the overall agent loop consists of observing the environment, optionally retrieving memory, computing an action, receiving feedback, and optionally updating memory through formation and evolution. Different agent implementations instantiate different subsets of these operations at different temporal frequencies, giving rise to memory systems that range from passive buffers to actively evolving knowledge bases.

## 2.3 Comparing Agent Memory with Other Key Concepts

Despite the growing interest in agentic systems endowed with memory, the community’s understanding of what constitutes *agent memory* remains fragmented. In practice, researchers and practitioners often conflate agent memory with related constructs such as LLM memory ([Wu et al., 2025g](#)), retrieval-augmented generation (RAG) ([Gao et al., 2024](#)), and context engineering ([Mei et al., 2025](#)). Although these concepts are intrinsically connected by their involvement in how information is managed and utilized in LLM-driven systems, they differ in scope, temporal characteristics, and functional roles.

These overlapping yet distinct notions have led to ambiguity in the literature and practice. To clarify these distinctions and situate agent memory within this broader landscape, we examine how agent memory *relates to*, and *diverges from*, LLM memory, RAG, and context engineering in the subsequent subsubsections. Figure 2 visually illustrates the commonalities and distinctions among these fields through a Venn diagram.**Agent Memory**

- - **Self-Evolving Memory**  
  e.g., Memento, H2R
- - **Multimodal Memory**  
  e.g., Ella, ViloMem, M3-Agent
- - **Latent Memory**  
  e.g., MemoryLLM, M+, MemGen
- - **Parametric Memory**  
  e.g., Retroformer, Early experience
- - **RL-enabled Memory**  
  e.g., MemAgent, RMM, MemSearcher, MEM1, Mem-alpha, Memory-R1

**LLM Memory**

- - **Few-shot prompting**  
  e.g., CoT, PALM
- - **Self-Reflection**  
  e.g., Self-refine, CRITIC
- - **KV compression/reuse**  
  e.g., AutoCompressor, SnapKV
- - **Attention KV management**  
  e.g., Mixture-of-Memory
- - **Long context processing**  
  e.g., Mamba, Memformer, MoA, Sparseformer, NSA

**RAG**

- - **Memory graph**  
  e.g., Zep, AriGraph
- - **Agentic memory**  
  e.g., A-Mem, G-Memory
- - **Working memory**  
  e.g., HiAgent, ReSum,
- - **Modular RAG**  
  e.g., FlashRAG, ComposeRAG
- - **Graph RAG**  
  e.g., LightRAG, HippoRAG
- - **Agentic RAG**  
  e.g., PlanRAG, Self-RAG

**Context Engineering**

- - **Tool-integrated reasoning**  
  e.g., ReTool, ToolLLM, Toolformer, VTool-R1, ToRL
- - **Tool selection**  
  e.g., AutoTool, VisTA
- - **Communication protocol**  
  e.g., ANP, A2A, MCP, Agora

**Figure 2** Conceptual comparison of **Agent Memory** with **LLM Memory**, **RAG**, and **Context Engineering**. The diagram illustrates shared technical implementations (e.g., KV reuse, graph retrieval) while highlighting fundamental distinctions: unlike the architectural optimizations of LLM Memory, the static knowledge access of RAG, or the transient resource management of Context Engineering, Agent Memory is uniquely characterized by its focus on maintaining a persistent and self-evolving cognitive state that integrates factual knowledge and experience. The listed categories and examples are illustrative rather than strictly parallel, serving as representative reference points to clarify conceptual relationships rather than to define a rigid taxonomy.

### 2.3.1 Agent Memory vs. LLM Memory

At a high level, *agent memory* almost fully subsumes what has traditionally been referred to as *LLM memory*. Since 2023, many works describing themselves as “LLM memory mechanisms” (Zhong et al., 2024; Packer et al., 2023a; Wang et al., 2023b) are more appropriately interpreted, under contemporary terminology, as early instances of agent memory. This reinterpretation arises from the historical ambiguity surrounding the very notion of an “LLM agent.” During 2023–2024, the community had no stable or coherent definition: in some cases, prompting an LLM to call a calculator already sufficed to qualify the system as an agent (Wu et al., 2024c); in other cases, agency required substantially richer capabilities such as explicit planning, tool use, memory, and reflective reasoning (Ruan et al., 2023). Only recently has a more unified and structured definition begun to emerge (e.g., LLM-based agent = LLM + reasoning + planning + memory + tool use + self-improvement + multi-turn interaction + perception, as discussed by Zhang et al. (2025f)), though even this formulation is not universally applicable. Against this historical backdrop, early systems such as MemoryBank (Zhong et al., 2024) and MemGPT (Packer et al., 2023a) framed their contributions as providing *LLM memory*. Yet what they fundamentally addressed were classical agentic challenges, for example enabling an LLM-based conversational agent to track user preferences, maintain dialogue-state information, and accumulate experience across multi-turn interactions. Under a modern and more mature understanding of agency, such systems are naturally categorized as instances of *agent memory*.

That said, the subsumption is not absolute. A distinct line of research genuinely concerns *LLM-internal memory*: managing the transformer’s key–value (KV) cache, designing long-context processing mechanisms, or modifying model architectures (e.g., RWKV (Peng et al., 2023), Mamba (Gu and Dao, 2024; Lieber et al., 2024), diffusion-based LMs (Nie et al., 2025)) to better retain information as sequence length grows. These works focus on intrinsic model dynamics and typically address tasks that do not require agentic behavior, and thus should be considered outside the scope of agent memory.**Overlap** Within our taxonomy, the majority of what has historically been called “LLM memory” corresponds to forms of agent memory. Techniques such as *few-shot prompting* (Prabhumoye et al., 2022; Ma et al., 2023a) can be viewed as a form of long-term memory, where past exemplars or distilled task summaries serve as reusable knowledge incorporated through retrieval or context injection. *Self-reflection* and iterative refinement methods (Madaan et al., 2023; Mousavi et al., 2023; Han et al., 2025c) naturally align with short-term, inside-trial memory, as the agent repeatedly leverages intermediate reasoning traces or outcomes from prior attempts within the same task. Even *KV compression* and context-window management (Yoon et al., 2024; Jiang et al., 2023), when used to preserve salient information across the course of a single task, function as short-term memory mechanisms in an agentic sense. These techniques all support the agent’s ability to accumulate, transform, and reuse information throughout a task’s execution.

**Distinctions** In contrast, memory mechanisms that intervene directly in the model’s internal state—such as architectural modifications for longer effective context, cache rewriting strategies, recurrent-state persistence, attention-sparsity mechanisms, or externalized KV-store expansions—are more appropriately classified as *LLM memory* rather than agent memory. Their goal is to expand or reorganize the representational capacity of the underlying model, not to furnish a decision-making agent with an evolving external memory base. They do not typically support cross-task persistence, environment-driven adaptation, or deliberate memory operations (e.g., formation, evolution, retrieval), and therefore lie outside the operational scope of agent memory as defined in this survey.

### 2.3.2 Agent Memory vs. RAG

At a conceptual level, *agent memory* and *retrieval-augmented generation* (RAG) exhibit substantial overlap: both systems construct, organize, and leverage auxiliary information stores to extend the capabilities of LLM/agents beyond their native parametric knowledge. For instance, structured representations such as knowledge graphs and indexing strategies appear in both communities’ methods, and recent developments in agentic RAG demonstrate how autonomous retrieval mechanisms can interact with dynamic databases in ways reminiscent of agent memory architectures (Singh et al., 2025). Indeed, the engineering stacks underlying many RAG and agent memory systems share common building blocks, including vector indices, semantic search, and context expansion modules.

Despite these technological convergences, the two paradigms have *historically* been distinguished by the contexts in which they are applied. Classical RAG techniques primarily augment an LLM with access to **static knowledge sources**, whether flat document stores, structured knowledge bases, or large corpora externally indexed to support retrieval on demand (Zhang et al., 2025q; Han et al., 2025b). These systems are designed to ground generation in up-to-date facts, mitigate hallucinations, and improve accuracy in knowledge-intensive tasks, but they generally do not maintain an internal, evolving memory of past interactions. In contrast, agent memory systems are instantiated within an agent’s **ongoing interaction with an environment**, continuously incorporating new information generated by the agent’s own actions and environmental feedback into a persistent memory base (Wang et al., 2024m; Zhao et al., 2024; Sun et al., 2025e).

In early formulations the distinction between RAG and agent memory was relatively clear: RAG retrieved from externally maintained knowledge for a single task invocation, whereas agent memory evolved over multi-turn, multi-task interaction. However, this boundary has become increasingly blurred as retrieval systems themselves become more dynamic. For example, certain retrieval tasks continuously update relevant context during iterative querying (e.g., multi-hop QA settings where related context is progressively added). Interestingly, systems such as HippoRAG/HippoRAG2 (Gutierrez et al., 2024; Gutiérrez et al., 2025) have been interpreted by both RAG and memory communities as addressing long-term memory challenges for LLMs. Consequently, a more practical (though not perfectly separable) distinction lies in the **task domain**. RAG is predominantly applied to augment LLMs with large, externally sourced context for individual inference tasks, exemplified by classical multi-hop and knowledge-intensive benchmarks such as HotpotQA (Yang et al., 2018), 2WikiMQA (Ho et al., 2020), and MuSiQue (Trivedi et al., 2022). By contrast, agent memory systems are typically evaluated in settings requiring sustained multi-turn interaction, temporal dependency, or environment-driven adaptation. Representative benchmarks include long-context dialogue evaluations such as LoCoMo (Maharana et al., 2024) and LongMemEval (Wu et al., 2025a), complex problem-solving and deep-research benchmarks such as GAIA (Mialon et al., 2023), XBench (Chen et al., 2025c), and BrowseComp (Wei et al., 2025b), code-centricagentic tasks such as SWE-bench Verified (Jimenez et al., 2024), as well as lifelong learning benchmarks such as StreamBench (Wu et al., 2024a). We provide a comprehensive summary of memory-related benchmarks in Section 6.1.

Nevertheless, even this domain-based distinction contains substantial gray areas. Many works self-described as agent memory systems are evaluated under long-document question-answering tasks such as HotpotQA (Wang et al., 2025g,p), while numerous papers foregrounded as RAG systems in fact implement forms of agentic self-improvement, continually distilling and refining knowledge or skills over time. As a result, titles, methodologies, and empirical evaluations frequently blur the conceptual boundary between the two paradigms. To further clarify these relationships, the following three paragraphs draw upon established taxonomies of RAG from (Mei et al., 2025): *modular RAG*, *graph RAG*, and *agentic RAG*, and examine how the core techniques associated with each lineage manifest within both RAG and agent memory systems.

**Modular RAG** Modular RAG refers to architectures in which the retrieval pipeline is decomposed into clearly specified components, such as indexing, candidate retrieval, reranking, filtering, and context assembly, that operate in a largely static and pipeline-like fashion (Singh et al., 2025). These systems treat retrieval as a well-engineered, modular subsystem external to the LLM, designed primarily for injecting relevant knowledge into the model’s context window during inference. Within the agent memory perspective, the corresponding techniques typically appear in the *retrieval stage*, where memory access is realized through vector search, semantic similarity matching, or rule-based filtering, as seen in popular agent memory frameworks like Memary (Memary, 2025), MemOS (Li et al., 2025l), and Mem0 (Chhikara et al., 2025).

**Graph RAG** Graph RAG systems structure the knowledge base as a graph, ranging from knowledge graphs to concept graphs or document-entity relations, and leverage graph traversal or graph-based ranking algorithms to retrieve context (Peng et al., 2024). This representation enables multi-hop relational reasoning, which has proven effective for knowledge-intensive tasks (Edge et al., 2025; Han et al., 2025b; Dong et al., 2025a). In the context of agent memory, graph-structured memory arises naturally when agents accumulate relational insights over time, such as linking concepts, tracking dependencies among subtasks, or recording causal relations inferred through interaction. Several well-established practices include Mem0<sup>g</sup> (Chhikara et al., 2025), A-MEM (Xu et al., 2025c), Zep (Rasmussen et al., 2025), and G-memory (Zhang et al., 2025c). Notably, graph-based agent memory systems may *construct*, *extend*, or *reorganize* its internal graph throughout the agent’s operation. Consequently, graph-based retrieval forms the structural backbone for both paradigms, but only agent memory treats the graph as a living, evolving representation of experience. We provide further analysis on graph-based memory forms in Section 3.1.2 and also refer the readers to a relevant survey (Liu et al., 2025h).

**Agentic RAG** Agentic RAG integrates retrieval into an autonomous decision-making loop, where an LLM agent actively controls when, how, and what to retrieve (Singh et al., 2025; Sun et al., 2025e). These systems often employ iterative querying, multi-step planning, or self-directed search procedures, enabling the agent to refine its information needs through deliberate reasoning, as implemented in PlanRAG (Lee et al., 2024b) and Self-RAG (Asai et al., 2023). For a more detailed understanding of agentic RAG, we refer the readers to Singh et al. (2025). From the agent memory perspective, agentic RAG occupies the closest conceptual space: both systems involve autonomous interaction with an external information store, both support multi-step refinement, and both may incorporate retrieved insights into subsequent reasoning. The key distinction is that classical agentic RAG typically operates over an *external* and often task-specific database, whereas agent memory maintains an *internal*, *persistent*, and *self-evolving* memory base that accumulates knowledge across tasks (Yan et al., 2025b; Xu et al., 2025c).

### 2.3.3 Agent Memory vs. Context Engineering

The relationship between *agent memory* and *context engineering* is best understood as an intersection of distinct operational paradigms rather than a hierarchical subsumption. Context engineering is a systematic design methodology that treats the context window as a constrained computational resource. It rigorously optimizes the information payload, including instructions, knowledge, state, and memory, to mitigate the asymmetry between massive input capacity and the model’s generation capability (Mei et al., 2025). Whileagent memory focuses on the **cognitive modeling** of a persistent entity with an evolving identity, context engineering operates under a **resource management** paradigm. From the perspective of context engineering, agent memory is merely one variable within the context assembly function that requires efficient scheduling to maximize inference efficacy. Conversely, from the perspective of an agent, context engineering serves as the implementation layer that ensures cognitive continuity remains within the physical limits of the underlying model.

**Overlap** The two fields converge significantly in the technical realization of working memory during long-horizon interactions and often employ functionally identical mechanisms to address the constraints imposed by a finite context window (Hu et al., 2025a; Zhang et al., 2025r; Kang et al., 2025c; Yu et al., 2025a). Both paradigms rely on advanced information compression (Zhou et al., 2025b; Wu et al., 2025f), organization (Xu et al., 2025c; Zhang et al., 2025c; Anokhin et al., 2024), and selection (Zhang et al., 2025r) techniques to preserve operational continuity over extended interaction sequences. For example, token pruning and importance-based selection methods (Jiang et al., 2023; Li et al., 2023c) that are central to context engineering frameworks play a fundamental role in agentic memory systems by filtering noise and retaining salient information. Similarly, the rolling summary technique serves as a shared foundational primitive, functioning simultaneously as a buffer management strategy and a transient episodic memory mechanism (Yu et al., 2025a; Lu et al., 2025b). In practice, the boundary between engineering the context and maintaining an agent’s short-term memory effectively dissolves in these scenarios, as both rely on the same underlying summarization, dynamic information retrieval, and recursive state updates (Tang et al., 2025b; Yoon et al., 2024).

**Distinctions** The distinction becomes most pronounced when moving beyond short-term text processing to the broader scope of long-lived agents. Context engineering primarily addresses the *structural organization* of the interaction interface between LLMs and their operational environment. This includes optimizing tool-integrated reasoning and selection pipelines (Qin et al., 2024a; Schick et al., 2023; Jia and Li, 2025) and standardizing communication protocols, such as MCP (Qiu et al., 2025c). These methods focus on ensuring that instructions, tool calls, and intermediate states are correctly formatted, efficiently scheduled, and executable within the constraints of the context window. As such, context engineering operates at the level of *resource allocation and interface correctness*, emphasizing syntactic validity and execution efficiency.

In contrast, agent memory defines a substantially broader cognitive scope. Beyond transient context assembly, it encompasses the persistent storage of factual knowledge (Zhong et al., 2024), the accumulation and evolution of experiential traces (Zhao et al., 2024; Tang et al., 2025d; Zhang et al., 2025d), and, in some cases, the internalization of memory into model parameters (Wang et al., 2025o). Rather than managing how information is presented to the model at inference time, agent memory governs what the agent *knows*, what it *has experienced*, and how these elements evolve over time. This includes consolidating repeated interactions into knowledge (Tan et al., 2025c), abstracting procedural knowledge from past successes and failures (Ouyang et al., 2025), and maintaining a coherent identity across tasks and episodes (Wang et al., 2024f).

From this perspective, context engineering constructs the external scaffolding that enables perception and action under resource constraints, whereas agent memory constitutes the internal substrate that supports learning, adaptation, and autonomy. The former optimizes the momentary interface between the agent and the model, while the latter sustains a persistent cognitive state that extends beyond any single context window.

### 3 Form: What Carries Memory?

As a starting point for organizing prior work, we begin by examining the most fundamental representational units out of which agent memory can be constructed. We first try to answer: what architectural or representational forms can agent memory take?

Across diverse agent systems, memory is not realized through a single, unified structure. Instead, different task settings call for different storage forms, each with its own structural properties. These architectures endow memory with distinct capabilities, shaping how an agent accumulates information over interactions and maintains behavioral consistency. They ultimately enable memory to fulfill its intended roles across varied task scenarios.Based on where memory resides and in what form it is represented, we organize these memories into three categories:

### Three Major Memory Forms

1. 1. **Token-level Memory** (Section 3.1): Memory organized as explicit and discrete units that can be individually accessed, modified, and reconstructed. These units remain externally visible and can be stored in a structured form over time.
2. 2. **Parametric Memory** (Section 3.2): Memory stored within the model parameters, where information is encoded through the statistical patterns of the parameter space and accessed implicitly during forward computation.
3. 3. **Latent Memory** (Section 3.3): Memory represented in the model’s internal hidden states, continuous representations, or evolving latent structures. It can persist and update during inference or across interaction cycles, capturing context-dependent internal states.

The three memory forms outlined above establish the core structural framework for understanding “what carries memory”. Each form organizes, stores, and updates information in its own way, giving rise to distinct representational patterns and operational behaviors. With this structural taxonomy in place, we can more systematically examine why agents need memory (Section 4) and how memory evolves, adapts, and shapes agent behavior over sustained interactions (Section 5). This classification provides the conceptual foundation for the discussions that follow.

## 3.1 Token-level Memory

### Definition of Token-level Memory

Token-level memory stores information as persistent, discrete units that are externally accessible and inspectable. The token here is a broad representational notion: beyond text tokens, it includes visual tokens, audio frames—any discrete element that can be written, retrieved, reorganized, and revised outside model parameters.

Because these units are explicit, token-level memory is typically transparent, easy to edit, and straightforward to interpret, making it a natural layer for retrieval, routing, conflict handling, and coordination with parametric and latent memory. Token-level memory is also the most common memory form and the one with the largest body of existing work.

Although all token-level memories share the property of being stored as discrete units, they differ significantly in how these units are organized. The structural organization of stored tokens plays a central role in determining how efficiently the agent can search, update, or reason over past information. To describe these differences, we categorize token-level memory by inter-unit structural organization, moving from no explicit topology to multi-layer topologies:

### Three Major Types of Token-level Memory

1. 1. **Flat Memory (1D)**: No explicit inter-unit topology. Memories are accumulated as sequences or bags of units (e.g., snippets, trajectories, chunks)
2. 2. **Planar Memory (2D)**: A structured but single-layer organization within one plane: units are related by a graph, tree, table and so on, with no cross-layer relations. The structure is explicit, but not layered.
3. 3. **Hierarchical Memory (3D)**: Structured across multiple layers with inter-layer links, forming a volumetric or stratified memory**(a) Flat Memory (1D)**

- **Experience**: e.g., ExpeL, AWM, ReasoningBank
- **Chunk**: e.g., Nemori, Memo, MemOS
- **Dialogue**: e.g., MemGPT, MemoryBank
- **Summary**: e.g., Think-in-Memory, RMM

**(b) Planar Memory (2D)**

- **Graph**: Memory graphs with different node/edge types. e.g., A-Mem, Memo^g, M3-Agent, D-SMART
- **Tree**: e.g., MemTree, TME, others

**(c) Hierarchical (3D)**

- **Pyramid**: e.g., G-Memory, CAM, others
- **Multi-Layer**: HiAgent, HippoRAG, SGMem

**Figure 3** Taxonomy of token-level memory organized by topological complexity and dimensionality: (a) **Flat Memory (1D)** stores information as linear sequences or independent clusters without explicit inter-unit topology, commonly used for *Chunk* sets, *Dialogue* logs, and *Experience* pools. (b) **Planar Memory (2D)** introduces a single-layer structured layout where units are linked via **Tree** or **Graph** structures to capture relational dependencies, supporting diverse node types such as images and chat records. (c) **Hierarchical Memory (3D)** employs multi-level forms, such as **Pyramids** or **Multi-layer** graphs, to facilitate vertical abstraction and cross-layer reasoning between different data granularities, such as raw docs and synthesized QAs.

The three types of token-level memory are clearly illustrated in Figure 3. From Flat Memory with no topology, to Planar Memory with single-layer structural organization, to Hierarchical Memory with multi-layer interlinked structures, this organizational spectrum governs not only how token-level memory supports search, update, and reasoning, but also how the memory itself is structured and what capabilities it affords. In the subsections that follow, we introduce each organizational form in terms of its strengths and limitations, typical use cases, and representative work. The summary and comparison of representative token-level memory methods are presented in Table 1.

It is worth noting that, following the idea introduced by ReAct (Yao et al., 2023b), a series of studies began focusing on long-horizon interaction tasks (Song et al., 2025a; Jin et al., 2025; Li et al., 2025h,e,j; Wu et al., 2025b). Many of these tasks introduce an explicit notion of memory, and because the memory is generally stored in plaintext form, they fall within the scope of token-level memory. Most of them emphasize how to compress or fold accumulated interaction traces so that agents can operate over long sequences without exceeding context limits (Zhou et al., 2025b; Zhang et al., 2025r; Wu et al., 2025f; Sun et al., 2025b; Li et al., 2025i; Chen et al., 2025b). A more detailed discussion is provided in Section 4.3 about working memory.### 3.1.1 Flat Memory (1D)

#### Definition of Flat (1D) Memory

Flat Memory stores information as accumulations of discrete units, without explicitly modeling semantic or relational dependencies among them. These units may include text chunks, user profiles, experience trajectories, their corresponding vector representations, or multimodal entries. Relationships among these units are not encoded directly in the memory.

To facilitate a clear and coherent presentation, we group prior work on flat memory according to their primary design objectives and technical emphases. This grouping serves **an organizational purpose** and does not imply that the resulting categories are strictly parallel or mutually exclusive. In practice, certain methods may be applicable to multiple categories, and some approaches involving multimodal information may be discussed in other sections when multimodality is not their central focus. Such an organization allows us to systematically review the literature while preserving flexibility in interpretation.

**Table 1** Comparison of representative token-level memory methods. We categorize existing works into three groups based on their topological complexity: **Flat Memory (1D)** for linear or independent records, **Planar Memory (2D)** for structured single-layer graphs/trees, and **Hierarchical Memory (3D)** for multi-level architectures. Methods are characterized across four dimensions: (1) **Multi** indicates multimodal capability, where ✓ denotes support for modalities beyond text (e.g., visual) and ✗ implies text-only; (2) **Type** identifies the specific functional category of the memory (e.g., *Fact* for factual memory, *Exp* for experiential memory, *Work* for working memory ); (3) **Memory Form** details the content of the stored units; and (4) **Task** lists the primary application domains.

<table border="1">
<thead>
<tr>
<th>Method</th>
<th>Multi</th>
<th>Type</th>
<th>Memory Form</th>
<th>Task</th>
</tr>
</thead>
<tbody>
<tr>
<td colspan="5" style="text-align: center;"><i>Flat Memory Models</i></td>
</tr>
<tr>
<td>Reflexion (Shinn et al., 2023b)</td>
<td>✗</td>
<td>E&amp;W</td>
<td>Trajectory as short-term and feedback as long-term</td>
<td>QA, Reasoning, Coding</td>
</tr>
<tr>
<td>Memento (Zhou et al., 2025a)</td>
<td>✗</td>
<td>Exp</td>
<td>Trajectory case (success/failure).</td>
<td>Reasoning</td>
</tr>
<tr>
<td>JARVIS-1 (Wang et al., 2025q)</td>
<td>✓</td>
<td>Exp</td>
<td>Plan-environment pairs.</td>
<td>Game</td>
</tr>
<tr>
<td>Expel (Zhao et al., 2024)</td>
<td>✗</td>
<td>Exp</td>
<td>Insights and few-shot examples.</td>
<td>Reasoning</td>
</tr>
<tr>
<td>Buffer of Thoughts (Yang et al., 2024b)</td>
<td>✗</td>
<td>Exp</td>
<td>High-level thought-templates.</td>
<td>Game, Reasoning, Coding</td>
</tr>
<tr>
<td>SAGE (Liang et al., 2025)</td>
<td>✗</td>
<td>Exp</td>
<td>Dual-store with forgetting mechanism.</td>
<td>Game, Reasoning, Coding</td>
</tr>
<tr>
<td>ChemAgent (Tang et al., 2025c)</td>
<td>✗</td>
<td>Exp</td>
<td>Structured sub-tasks and principles.</td>
<td>Chemistry</td>
</tr>
<tr>
<td>AgentKB (Tang et al., 2025d)</td>
<td>✗</td>
<td>Exp</td>
<td>5-tuple experience nodes.</td>
<td>Coding, Reasoning</td>
</tr>
<tr>
<td>H<sup>2</sup>R (Ye et al., 2025b)</td>
<td>✗</td>
<td>Exp</td>
<td>Planning and Execution layers.</td>
<td>Game, Embodied Simulation</td>
</tr>
<tr>
<td>AWM (Wang et al., 2024m)</td>
<td>✗</td>
<td>Exp</td>
<td>Abstracted universal workflows.</td>
<td>Web</td>
</tr>
<tr>
<td>PRINCIPLES (Kim et al., 2025a)</td>
<td>✗</td>
<td>Exp</td>
<td>Rule templates from self-play.</td>
<td>Emotional Companion</td>
</tr>
<tr>
<td>ReasoningBank (Ouyang et al., 2025)</td>
<td>✗</td>
<td>Exp</td>
<td>Transferable reasoning strategy items.</td>
<td>Web</td>
</tr>
<tr>
<td>Voyager (Wang et al., 2024b)</td>
<td>✓</td>
<td>Exp</td>
<td>Executable skill code library.</td>
<td>Game</td>
</tr>
<tr>
<td>DGM (Zhang et al., 2025i)</td>
<td>✗</td>
<td>Exp</td>
<td>Recursive self-modifiable codebase.</td>
<td>Coding</td>
</tr>
<tr>
<td>Memp (Fang et al., 2025d)</td>
<td>✗</td>
<td>Exp</td>
<td>Instructions and abstract scripts.</td>
<td>Embodied Simulation, Travel Planning</td>
</tr>
<tr>
<td>UFO2 (Zhang et al., 2025a)</td>
<td>✓</td>
<td>Exp</td>
<td>System docs and interaction records.</td>
<td>Windows OS</td>
</tr>
<tr>
<td>LEGOMem (Han et al., 2025a)</td>
<td>✗</td>
<td>Exp</td>
<td>Vectorized task trajectories.</td>
<td>Office</td>
</tr>
<tr>
<td>ToolMem (Xiao et al., 2025b)</td>
<td>✗</td>
<td>Exp</td>
<td>Tool capability.</td>
<td>Tool Calling</td>
</tr>
<tr>
<td>SCM (Wang et al., 2025a)</td>
<td>✗</td>
<td>Fact</td>
<td>Memory stream and vector database.</td>
<td>Long-context</td>
</tr>
<tr>
<td>MemoryBank (Zhong et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>History and user profile.</td>
<td>Emotional Companion</td>
</tr>
<tr>
<td>MPC (Lee et al., 2023)</td>
<td>✗</td>
<td>Fact</td>
<td>Persona and summary vector pool.</td>
<td>QA</td>
</tr>
<tr>
<td>RecMind (Wang et al., 2024h)</td>
<td>✗</td>
<td>Fact</td>
<td>User metadata and external knowledge.</td>
<td>Recommendation</td>
</tr>
<tr>
<td>InteRecAgent (Huang et al., 2025d)</td>
<td>✗</td>
<td>Fact</td>
<td>User profiles and candidate item.</td>
<td>Recommendation</td>
</tr>
<tr>
<td>Ego-LLaVA (Shen et al., 2024)</td>
<td>✓</td>
<td>Fact</td>
<td>Language-encoded chunk embeddings.</td>
<td>Multimodal QA</td>
</tr>
<tr>
<td>ChatHaruhi (Li et al., 2023a)</td>
<td>✗</td>
<td>Fact</td>
<td>Dialogue database from media.</td>
<td>Role-Playing</td>
</tr>
<tr>
<td>Memochat (Lu et al., 2023)</td>
<td>✗</td>
<td>Fact</td>
<td>Memos and categorized dialogue history.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>RecursiveSum (Wang et al., 2025h)</td>
<td>✗</td>
<td>Fact</td>
<td>Recursive summaries of short dialogues.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>MemGPT (Packer et al., 2023a)</td>
<td>✗</td>
<td>Fact</td>
<td>Virtual memory (Main/External contexts).</td>
<td>Long-conv QA, Doc QA</td>
</tr>
</tbody>
</table>

Continued on next page**Table 1** Comparison of representative token-level memory methods. We categorize existing works into three groups based on their topological complexity: **Flat Memory (1D)** for linear or independent records, **Planar Memory (2D)** for structured single-layer graphs/trees, and **Hierarchical Memory (3D)** for multi-level architectures. Methods are characterized across four dimensions: (1) **Multi** indicates multimodal capability, where ✓ denotes support for modalities beyond text (e.g., visual) and ✗ implies text-only; (2) **Type** identifies the specific functional category of the memory (e.g., *Fact* for factual memory, *Exp* for experiential memory, *Work* for working memory ); (3) **Memory Structure** details the organization mechanism of the stored units; and (4) **Task** lists the primary application domains. (continued)

<table border="1">
<thead>
<tr>
<th>Method</th>
<th>Multi</th>
<th>Type</th>
<th>Memory Structure</th>
<th>Task</th>
</tr>
</thead>
<tbody>
<tr>
<td>RoleLLM (Wang et al., 2024d)</td>
<td>✗</td>
<td>Fact</td>
<td>Role-specific QA pairs.</td>
<td>Role-Playing</td>
</tr>
<tr>
<td>Think-in-memory (Liu et al., 2023a)</td>
<td>✗</td>
<td>Fact</td>
<td>Hash table of inductive thoughts.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>PLA (Yuan et al., 2025b)</td>
<td>✗</td>
<td>Fact</td>
<td>Evolving records of history and summaries.</td>
<td>QA, Human Feedback</td>
</tr>
<tr>
<td>COMEDY (Chen et al., 2025d)</td>
<td>✗</td>
<td>Fact</td>
<td>Single-model compressed memory format.</td>
<td>Summary, Compression, QA</td>
</tr>
<tr>
<td>Memoro (Zulfikar et al., 2024)</td>
<td>✓</td>
<td>Fact</td>
<td>Speech-to-text vector embeddings.</td>
<td>User Study</td>
</tr>
<tr>
<td>Memory Sharing (Gao and Zhang, 2024a)</td>
<td>✗</td>
<td>Fact</td>
<td>Query-Response pair retrieval.</td>
<td>Literary Creation, Logic, Plan Generation</td>
</tr>
<tr>
<td>Conv Agent (Alonso et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>Chain-of-tables and vector entries.</td>
<td>QA</td>
</tr>
<tr>
<td>EM-LLM (Fountas et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Episodic events with Bayesian boundaries.</td>
<td>Long-context</td>
</tr>
<tr>
<td>Memocrs (Xi et al., 2024a)</td>
<td>✗</td>
<td>Fact</td>
<td>User metadata and knowledge.</td>
<td>Recommendation</td>
</tr>
<tr>
<td>SECOM (Pan et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Paragraph-level segmented blocks.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Mem0 (Chhikara et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Summary and original dialogue.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>RMM (Tan et al., 2025c)</td>
<td>✗</td>
<td>Fact</td>
<td>Reflection-organized flat entries.</td>
<td>Personalization</td>
</tr>
<tr>
<td>MEMENTO (Kwon et al., 2025)</td>
<td>✓</td>
<td>Fact</td>
<td>Interaction history entries.</td>
<td>Personalization</td>
</tr>
<tr>
<td>MemGuide (Du et al., 2025b)</td>
<td>✗</td>
<td>Fact</td>
<td>Dialogue-derived QA pairs.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>MIRIX (Wang and Chen, 2025)</td>
<td>✓</td>
<td>Fact</td>
<td>Six optimized flat memory types.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>SemanticAnchor (Chatterjee and Agarwal, 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Syntactic 5-tuple structure.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>MMS (Zhang et al., 2025b)</td>
<td>✗</td>
<td>Fact</td>
<td>Dual Retrieval and Context units.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Memory-R1 (Yan et al., 2025c)</td>
<td>✗</td>
<td>Fact</td>
<td>RL-managed mem0 architecture.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>ComoRAG (Wang et al., 2025f)</td>
<td>✗</td>
<td>Fact</td>
<td>Fact/Semantic/Plot units with probes.</td>
<td>Narrative QA</td>
</tr>
<tr>
<td>Nemori (Nan et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Predictive calibration store.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Livia (Xi and Wang, 2025)</td>
<td>✓</td>
<td>Fact</td>
<td>Pruned interaction history.</td>
<td>Emotional Companion</td>
</tr>
<tr>
<td>MOOM (Chen et al., 2025e)</td>
<td>✗</td>
<td>Fact</td>
<td>Decoupled plot and character stores.</td>
<td>Role-Playing</td>
</tr>
<tr>
<td>Mem-<math>\alpha</math> (Wang et al., 2025p)</td>
<td>✗</td>
<td>Fact</td>
<td>Core, Semantic, and Episodic Mem.</td>
<td>Memory Management</td>
</tr>
<tr>
<td>Personalized Long term Interaction (Westhäuser et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Hierarchical history and summaries.</td>
<td>Personalization</td>
</tr>
<tr>
<td>LightMem (Fang et al., 2025b)</td>
<td>✗</td>
<td>Fact</td>
<td>Optimized Long/Short-term store.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>MEXTRA (Wang et al., 2025b)</td>
<td>✗</td>
<td>Fact</td>
<td>Extracted raw dialogue data.</td>
<td>Privacy Attack</td>
</tr>
<tr>
<td>MovieChat (Song et al., 2024)</td>
<td>✓</td>
<td>Fact</td>
<td>Short-term features and long-term persistence.</td>
<td>Video Understanding</td>
</tr>
<tr>
<td>MA-LMM (He et al., 2024)</td>
<td>✓</td>
<td>Fact</td>
<td>Visual and Query memory banks.</td>
<td>Video Understanding</td>
</tr>
<tr>
<td>VideoAgent (Wang et al., 2024g)</td>
<td>✓</td>
<td>Fact</td>
<td>Temporal text descriptions and object tracking.</td>
<td>Video Understanding</td>
</tr>
<tr>
<td>Video-RAG (Luo et al., 2025b)</td>
<td>✓</td>
<td>Fact</td>
<td>Visually-aligned information .</td>
<td>Video Understanding</td>
</tr>
<tr>
<td>KARMA (Wang et al., 2025r)</td>
<td>✓</td>
<td>Fact</td>
<td>3D scene graph and dynamic object states.</td>
<td>Embodied Task</td>
</tr>
<tr>
<td>Embodied VideoAgent (Fan et al., 2025)</td>
<td>✓</td>
<td>Fact</td>
<td>Persistent object and sensor store.</td>
<td>MultiModal</td>
</tr>
<tr>
<td>Mem2Ego (Zhang et al., 2025m)</td>
<td>✓</td>
<td>Fact</td>
<td>Map, landmark, and visited location stores.</td>
<td>Embodied Navigation</td>
</tr>
<tr>
<td>Context-as-Memory (Yu et al., 2025b)</td>
<td>✓</td>
<td>Fact</td>
<td>Generated context frames.</td>
<td>Video Generation</td>
</tr>
<tr>
<td>RCR-Router (Liu et al., 2025d)</td>
<td>✗</td>
<td>Fact</td>
<td>Budget-aware semantic subsets.</td>
<td>QA</td>
</tr>
<tr>
<td>ELL (Cai et al., 2025a)</td>
<td>✗</td>
<td>Fact</td>
<td>Lifelong memory and skills.</td>
<td>Lifelong Learning</td>
</tr>
<tr>
<td>MemRL (Zhang et al., 2026)</td>
<td>✗</td>
<td>Exp</td>
<td>RL for memory management.</td>
<td>Web</td>
</tr>
<tr>
<td>ReMe (Cao et al., 2025b)</td>
<td>✗</td>
<td>Exp</td>
<td>Step level experience and insight.</td>
<td>Web</td>
</tr>
<tr>
<td>MMAG (Zeppieri, 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Five interacting memory layers.</td>
<td>User Study</td>
</tr>
<tr>
<td>Hindsight (Latimer et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Retains, recalls, and reflects.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>GAM (Yan et al., 2025a)</td>
<td>✗</td>
<td>Fact</td>
<td>Simple memory but search is guided.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td colspan="5" style="text-align: center;"><i>Planar Memory Models</i></td>
</tr>
<tr>
<td>D-SMART (Lei et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Structured memory with reasoning trees.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Reflexion (Shinn et al., 2023b)</td>
<td>✗</td>
<td>Work</td>
<td>Reflective text buffer from experiences.</td>
<td>QA, Reasoning, Coding</td>
</tr>
<tr>
<td colspan="4"></td>
<td style="text-align: right;">Continued on next page</td>
</tr>
</tbody>
</table>**Table 1** Comparison of representative token-level memory methods. We categorize existing works into three groups based on their topological complexity: **Flat Memory (1D)** for linear or independent records, **Planar Memory (2D)** for structured single-layer graphs/trees, and **Hierarchical Memory (3D)** for multi-level architectures. Methods are characterized across four dimensions: (1) **Multi** indicates multimodal capability, where ✓ denotes support for modalities beyond text (e.g., visual) and ✗ implies text-only; (2) **Type** identifies the specific functional category of the memory (e.g., *Fact* for factual memory, *Exp* for experiential memory, *Work* for working memory ); (3) **Memory Structure** details the organization mechanism of the stored units; and (4) **Task** lists the primary application domains. (continued)

<table border="1">
<thead>
<tr>
<th>Method</th>
<th>Multi</th>
<th>Type</th>
<th>Memory Structure</th>
<th>Task</th>
</tr>
</thead>
<tbody>
<tr>
<td>PREMem (Kim et al., 2025b)</td>
<td>✗</td>
<td>Fact</td>
<td>Dynamic cross-session linked triples.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Query Reconstruct (Xu et al., 2025b)</td>
<td>✗</td>
<td>Exp</td>
<td>Logic graphs built from knowledge bases.</td>
<td>KnowledgeGraph QA</td>
</tr>
<tr>
<td>KGT (Sun et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>KG node from query and feedback.</td>
<td>QA</td>
</tr>
<tr>
<td>Optimus-1 (Li et al., 2024d)</td>
<td>✓</td>
<td>F&amp;E</td>
<td>Knowledge graph and experience pool.</td>
<td>Game</td>
</tr>
<tr>
<td>SALI (Pan et al., 2024)</td>
<td>✓</td>
<td>Exp</td>
<td>Topological graph with spatial nodes</td>
<td>Navigation</td>
</tr>
<tr>
<td>HAT (A et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>Hierarchical aggregate tree.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>MemTree (Rezazadeh et al., 2025c)</td>
<td>✗</td>
<td>Fact</td>
<td>Dynamic hierarchical conversation tree.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>TeaFarm (iunn Ong et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Causal edges connecting memories.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>COMET (Kim et al., 2024b)</td>
<td>✗</td>
<td>Fact</td>
<td>Context-aware memory through graph.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Intrinsic Memory (Yuen et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Private internal and shared external mem.</td>
<td>Planning</td>
</tr>
<tr>
<td>A-MEM (Xu et al., 2025c)</td>
<td>✗</td>
<td>Fact</td>
<td>Card-based connected mem.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Ret-LLM (Modarressi et al., 2023)</td>
<td>✗</td>
<td>Fact</td>
<td>Triplet table and LSH vectors.</td>
<td>QA</td>
</tr>
<tr>
<td>HuaTuo (Wang et al., 2023a)</td>
<td>✗</td>
<td>Fact</td>
<td>Medical Knowledge Graph.</td>
<td>Medical QA</td>
</tr>
<tr>
<td>M3-Agent (Long et al., 2025)</td>
<td>✓</td>
<td>Fact</td>
<td>Multimodal nodes in graph structure.</td>
<td>Embodied QA</td>
</tr>
<tr>
<td>EMem (Zhou and Han, 2025a)</td>
<td>✗</td>
<td>Fact</td>
<td>Event-centric alternative with pagerank.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>WorldMM (Yeo et al., 2025)</td>
<td>✓</td>
<td>Fact</td>
<td>Multiple complementary memories.</td>
<td>Video Understanding</td>
</tr>
<tr>
<td>Memoria (Sarin et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Knowledge-graph profile and summary.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>LingoEDU (Zhou et al., 2026)</td>
<td>✗</td>
<td>Fact</td>
<td>Relation tree of Elementary Discourse Units.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td colspan="5" style="text-align: center;"><i>Hierarchical Memory Models</i></td>
</tr>
<tr>
<td>GraphRAG (Edge et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Multi-level community graph indices.</td>
<td>QA, Summarization</td>
</tr>
<tr>
<td>H-Mem (Sun and Zeng, 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Decoupled index layers and content layers.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>EMG-RAG (Wang et al., 2024l)</td>
<td>✗</td>
<td>Fact</td>
<td>Three-tiered memory graph.</td>
<td>QA</td>
</tr>
<tr>
<td>G-Memory (Zhang et al., 2025c)</td>
<td>✗</td>
<td>Exp</td>
<td>Query-centric three-layer graph structure.</td>
<td>QA, Game, Embodied Task</td>
</tr>
<tr>
<td>Zep (Rasmussen et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>Temporal Knowledge Graphs.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>SGMem (Wu et al., 2025h)</td>
<td>✗</td>
<td>Fact</td>
<td>Chunk Graph and Sentence Graph.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>HippoRAG (Gutierrez et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>Knowledge with query nodes.</td>
<td>QA</td>
</tr>
<tr>
<td>HippoRAG 2 (Gutiérrez et al., 2025)</td>
<td>✗</td>
<td>Fact</td>
<td>KG with phrase and passage.</td>
<td>QA</td>
</tr>
<tr>
<td>AriGraph (Anokhin et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>Semantic and Episodic memory graph.</td>
<td>Game</td>
</tr>
<tr>
<td>Lyfe Agents (Kaiya et al., 2023)</td>
<td>✗</td>
<td>Fact</td>
<td>Working, Short &amp; Long-term layers.</td>
<td>Social Simulation</td>
</tr>
<tr>
<td>CAM (Li et al., 2025g)</td>
<td>✗</td>
<td>Fact</td>
<td>Multilayer graph with topic.</td>
<td>Doc QA</td>
</tr>
<tr>
<td>HiAgent (Hu et al., 2025a)</td>
<td>✗</td>
<td>E&amp;W</td>
<td>Goal graphs with recursive cluster.</td>
<td>Agentic Tasks</td>
</tr>
<tr>
<td>ILM-TR (Tang et al., 2024)</td>
<td>✗</td>
<td>Fact</td>
<td>Hierarchical Memory tree.</td>
<td>Long-context</td>
</tr>
<tr>
<td>CompassMem (Hu et al., 2026b)</td>
<td>✗</td>
<td>Fact</td>
<td>Hierarchical event-centric Memory.</td>
<td>QA</td>
</tr>
<tr>
<td>MAGMA (Jiang et al., 2026)</td>
<td>✗</td>
<td>Fact</td>
<td>Semantic, temporal, causal, entity graphs.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>EverMemOS (Hu et al., 2026a)</td>
<td>✗</td>
<td>Fact</td>
<td>Reusable memories covering multi types.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>RGMem (Tian et al., 2025a)</td>
<td>✗</td>
<td>Fact</td>
<td>Renormalization Group-based memory.</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>MemVerse (Liu et al., 2025e)</td>
<td>✓</td>
<td>Fact</td>
<td>Multimodal hierarchical knowledge graphs.</td>
<td>Reasoning, QA</td>
</tr>
</tbody>
</table>

**Dialogue** Some flat memory work focuses on storing and managing dialogue content. Early approaches primarily focused on preventing forgetting by storing raw dialogue history or generating recursive summaries to extend context windows (Wang et al., 2025a; Lu et al., 2023; Wang et al., 2025h; Yuan et al., 2025b). MemGPT (Packer et al., 2023a) introduces an operating-system metaphor with hierarchical management, inspiring subsequent works (Li et al., 2025l; Kang et al., 2025a) to decouple active context from external storage for infinite context management.

To improve retrieval precision, the granularity and structure of memory units have become increasingly diverseand cognitively aligned. Some works, like COMEDY (Chen et al., 2025d), Memory Sharing (Gao and Zhang, 2024a) and MemGuide (Du et al., 2025b) compress information into compact semantic representations or query-response pairs to facilitate direct lookup, while others, like Alonso et al. (2024) and MIRIX (Wang and Chen, 2025) adopt hybrid structures ranging from vector-table combinations to multi-functional memory types. Furthermore, research has begun to define memory boundaries based on cognitive psychology, organizing information through syntactic tuples (Chatterjee and Agarwal, 2025) or segmenting events based on Bayesian surprise and paragraph structures (Fountas et al., 2025; Pan et al., 2025), thereby matching human-like cognitive segmentation.

As conversational depth increases, memory evolves to store high-level cognitive processes and narrative complexities. Instead of mere factual records, systems like Think-in-Memory (Liu et al., 2023a) and RMM (Tan et al., 2025c) store inductive thoughts and retrospective reflections to guide future reasoning. In complex scenarios such as role-playing or long narratives, approaches like ComoRAG (Wang et al., 2025f) and MOOM (Chen et al., 2025e) decompose memory into factual, plot-level, and character-level components, ensuring the agent maintains a coherent persona and understanding across extended interactions.

Memory has transitioned from static storage to autonomous and adaptive optimization. Mem0 (Chhikara et al., 2025) established standardized operations for memory maintenance, laying the foundation for intelligent control. Recent advances introduce reinforcement learning to optimize memory construction (Yan et al., 2025c; Wang et al., 2025p), while other mechanisms focus on dynamic calibration and efficiency, such as predicting missing information (Nan et al., 2025), managing token budgets across multi-agent systems (Liu et al., 2025d), and reducing redundancy in long-term storage (Fang et al., 2025b).

**Preference** Some memory systems focus on modeling a user’s evolving tastes, interests, and decision patterns, especially in recommendation scenarios where preference understanding is central. Unlike dialogue-centric memory, which focuses on maintaining conversational coherence, preference memory centers on identifying a user’s tastes and tendencies. Early efforts such as RecMind (Wang et al., 2024h) separate user-specific information from external domain knowledge by storing both factual user attributes and item metadata. InteRecAgent (Huang et al., 2025d) folds memory into the recommendation workflow but focuses more on the current candidate set, keeping user profiles and the active item pool to support context-aware recommendations. MR.Rec (Huang et al., 2025b) builds a memory index archiving the full interaction process, storing raw item information and per-category preference summaries. In conversational settings, Memocrs (Xi et al., 2024a) proposes a more structured design with a user-specific memory tracking entities and user attitudes, and a general memory aggregating cross-user knowledge.

**Profile** A subset of flat memory systems focuses on storing and maintaining stable user profiles, character attributes, or long-term identity information so that agents can behave consistently across turns and tasks. MemoryBank (Zhong et al., 2024) represents one of the earliest frameworks in this direction: it organizes dialogue history and event summaries by timestamp, gradually building a user profile that supports accurate retrieval of identity-relevant information. AI Persona (Wang et al., 2024f) makes the memory system process information not only presented in the dialogue context but also from multi-dimensional human-AI interaction dimensions. MPC (Lee et al., 2023) extends this idea by storing real-time persona information and dialogue summaries in a memory pool, keeping conversation behavior aligned with a consistent persona over long interactions. Westhäuser et al. (2025) proposes a more comprehensive profile-maintenance mechanism, combining long-term and short-term memory with automatically generated summaries after each turn to form a mid-term context, allowing the user profile to evolve continuously through interaction.

In virtual role-playing settings, ChatHaruhi (Li et al., 2023a) extracts dialogue from novels and television scripts, enabling the model to maintain character-consistent behavior by retrieving memory. RoleLLM (Wang et al., 2024d) takes a more structured approach by building question-answer pairs to capture character-specific knowledge.

**Experience** Distinct from the static, general knowledge, experience memory stems from the agent’s dynamic accumulation during actual interaction tasks, encompassing specific observations, chains of thought, action trajectories, and environmental feedback. It is important to note that this section just provides a briefoverview of experiential memory strictly from the perspective of token-level storage; a more comprehensive analysis and detailed discussion of this domain will be presented in Section 4.2.

The most fundamental form of experience memory involves the direct archival of historical behavioral trajectories. This paradigm enables agents to inform current decision-making by retrieving and reusing past instances, encompassing both successful and failed cases (Zhou et al., 2025a; Wang et al., 2025q).

To address the limited generalizability inherent in raw trajectories, a significant body of research focuses on abstracting specific interactions into higher-level, generalized experiences. As one of the earliest and most influential approaches, Reflexion (Shinn et al., 2023b) distinguishes short-term memory as the trajectory history and long-term memory as the feedback produced by the self-reflection model. Certain studies compress complex interaction histories into universal workflows, rule templates, or high-level “thought-templates” to facilitate cross-problem transfer and reuse (Wang et al., 2024m; Kim et al., 2025a; Yang et al., 2024b). Other works emphasize the structural organization and dynamic maintenance of memory. These approaches ensure that stored insights remain adaptable to novel tasks and are efficiently updated by constructing domain-specific structured knowledge bases, employing hierarchical plan-execute memory architectures, or incorporating human-like forgetting and reflection mechanisms (Tang et al., 2025c,d; Ouyang et al., 2025; Ye et al., 2025b; Zhao et al., 2024; Liang et al., 2025).

In contexts involving programming or specific tool utilization, experience memory evolves into executable skills. Within this paradigm, agents consolidate exploration experiences into code repositories, procedural scripts, or tool-usage entries. Leveraging environmental feedback, these systems iteratively refine code quality or even dynamically modify their underlying logic to achieve self-evolution (Wang et al., 2024a; Yin et al., 2025; Fang et al., 2025d; Xiao et al., 2025b). Furthermore, targeting complex environments such as operating systems, some studies distill successful execution records into reusable exemplars or vectorized representations, thereby facilitating an efficient pipeline from offline construction to online allocation (Zhang et al., 2025a; Han et al., 2025a).

**Multimodal** Multimodal memory systems store information in the form of discrete token-level units extracted from raw multimodal data, such as images, video frames, audio segments, and text, enabling agents to capture, compress, and retrieve knowledge across channels and over long spans of experience. In wearable and egocentric settings, early work such as Ego-LLaVA (Shen et al., 2024) captures first-person video and converts it into lightweight language descriptions. Memoro (Zulfikar et al., 2024) follows a similar philosophy but uses speech-to-text to form embedding-based memory chunks. Building on this direction, Livia (Xi and Wang, 2025) incorporates long-term user memory into an AR system with emotional awareness, applying forgetting curves and pruning strategies.

For video understanding, the emphasis shifts toward separating transient visual cues from enduring contextual information. MovieChat (Song et al., 2024) adopts a short-term/long-term split, storing recent frame features. MA-LMM (He et al., 2024) pushes this further with a dual-bank design—one storing raw visual features and the other retaining query embeddings. VideoAgent (Wang et al., 2024g) adopts a more semantically organized approach, maintaining a temporal memory of textual clip descriptions alongside object-level memory that tracks entities across frames. In interactive video generation, Context-as-Memory (Yu et al., 2025b) shows that simply storing previously generated frames as memory can also be highly effective. WorldMM (Yeo et al., 2025) constructs multiple mutually reinforcing memory modules that capture information in both textual and visual modalities.

In embodied scenarios, memory becomes inherently tied to spatial structure and ongoing interaction. KARMA (Wang et al., 2025r) introduces a two-tier memory system: long-term memory stores static objects in a 3D scene graph, while short-term memory tracks object positions and state changes. Embodied VideoAgent (Fan et al., 2025) also builds persistent object memories but fuses them with first-person video and additional embodied sensors. Mem2Ego (Zhang et al., 2025m) extends this idea to navigation by separating global maps, landmark descriptions, and visitation histories into three distinct memory stores. Complementing these task-driven designs, MEMENTO (Kwon et al., 2025) provides an evaluation framework that treats multimodal interaction history as an agent’s memory, enabling systematic assessment of how well embodied systems utilize accumulated perceptual experience.**Discussion** The primary advantage of Flat Memory is their simplicity and scalability: memory can be appended or pruned with minimal cost, and retrieval methods such as similarity search allow flexible access without requiring predefined structure. This makes them suitable for broad recall, episodic accumulation, and rapidly changing interaction histories. However, the lack of explicit relational organization means that coherence and relevance depend heavily on retrieval quality. As the memory grows, redundancy and noise can accumulate, and the model may retrieve relevant units without understanding how they relate, limiting compositional reasoning, long-horizon planning, and abstraction formation. Thus, topology-free collections excel at broad coverage and lightweight updates, but are constrained in tasks requiring structured inference or stable knowledge organization.

### 3.1.2 Planar Memory (2D)

#### Definition of Planar (2D) Memory

Planar Memory introduces an explicit organizational topology among memory units, but only within a single structural layer, which for short called *2D*. The topology may be a graph, tree, table, implicit connection structure and so on, where relationships such as adjacency, parent-child ordering, or semantic grouping are encoded within one plane, without hierarchical levels or cross-layer references.

The core of Planar memory forms lies in breaking through a single storage pool by establishing explicit association mechanisms, achieving a leap from mere “storage” to “organization”.

**Tree** Tree structures organize information hierarchically and can handle different levels of abstraction. HAT (A et al., 2024) builds a Hierarchical Aggregate Tree by segmenting long interactions and then aggregating them step by step. This multi-level structure supports coarse-to-fine retrieval and performs better than flat vector indices in long-context question answering. To reduce dialogue fragmentation, MemTree (Rezazadeh et al., 2025c) introduces a dynamic representation that infers hierarchical schemas from isolated conversation logs. It gradually summarizes concrete events into higher-level concepts, allowing agents to use both detailed memories and abstract knowledge.

**Graph** Graph structures dominate the landscape of 2D memory due to their ability to capture complex associations, causality, and temporal dynamics. Foundational works like Ret-LLM (Modarressi et al., 2023) abstract external storage into addressable triple-based units, enabling the LLM to interact with a relation-centric table that functions like a lightweight knowledge graph. In the medical domain, HuaTuo (Wang et al., 2023a) injects professional knowledge by integrating a structured corpus of Chinese medical knowledge graphs and clinical texts to fine-tune the base model. KGT (Sun et al., 2024) introduces a real-time personalization mechanism where user preferences and feedback are encoded as nodes and edges in a user-specific knowledge graph. For reasoning-intensive tasks, PREMem (Kim et al., 2025b) shifts part of the inference burden to the memory construction phase, deriving structured memory items and their evolution relations from raw dialogue. Similarly, Memory-augmented Query Reconstruction (Xu et al., 2025b) maintains a dedicated query memory that records past KG queries and reasoning steps, using retrieved records to reconstruct more accurate queries. Building on a timeline perspective, TeaFarm (iunn Ong et al., 2025) organizes dialogue history along segmented timelines and applies structured compression to manage lifelong context. COMET (Kim et al., 2024b) further refines conversational memory by using external commonsense bases to parse dialogue and dynamically update a context-aware persona graph with inferred hidden attributes. A-Mem (Xu et al., 2025c) standardizes knowledge into card-like units. It organizes them by relevance and places related memories in the same box, which builds a complete memory network. Intrinsic Memory Agents (Yuen et al., 2025) employ a partitioned architecture in which sub-agents maintain their own role-specific private memories while collaboratively reading and writing to a shared memory. Extending to multimodal agents, M3-Agent (Long et al., 2025) unifies image, audio, and text into an entity-centric memory graph. SALI (Pan et al., 2024) constructs a Reality–Imagination Hybrid Memory, unifying real observations and imagined future scenarios into a consistent navigation graph.**Hybrid** Complex tasks often require hybrid architectures that segregate distinct cognitive functions while sharing a common memory substrate. Optimus-1 (Li et al., 2024d) explicitly separates static knowledge into a hierarchical directed knowledge graph for planning, and dynamic interactions into an abstract multimodal experience Pool for reflection and self-improvement. D-SMART (Lei et al., 2025) combines a structured factual memory, implemented as a continuously updated knowledge graph, with a traversal-based reasoning tree.

**Discussion** The Planar Memory, by effectively establishing links between its nodes, enables memories to leverage collective synergies and thus encode more comprehensive contextual knowledge. Moreover, it supports retrieval mechanisms that go beyond simple iteration, including structured key-value lookups and relational traversal along graph edges. These capabilities make the form strong in storing, organizing, and managing memories. However, it also faces a critical limitation: Without a hierarchical storage mechanism, all memories must be consolidated into a single, monolithic module. As task scenarios grow in complexity and diversity, this redundant and flattened design becomes increasingly inadequate for robust performance. More importantly, the high construction and search costs significantly hinder its practical deployment.

### 3.1.3 Hierarchical Memory (3D)

#### Definition of Hierarchical (3D) Memory

Hierarchical memory organizes information across layers, using inter-level connections to shape the memories into a volumetric structured space.

Such hierarchies support representations at different degrees of abstraction—from raw observations, to compact event summaries, to higher-level thematic patterns. Cross-layer connections further yield a volumetric memory space through which the system can navigate not only laterally among units but also vertically across abstraction levels.

Hierarchical Memory moves beyond simple stratification, aiming to build complex systems with deep abstraction capabilities and dynamic evolutionary mechanisms. These works typically employ multi-level graph structures or neuroscience-inspired mechanisms to build a more human-like volumetric memory space, where information is richer and the connections between memory units are clearer and more explicit.

**Pyramid** This category constructs memory as multi-level pyramids, where information is progressively organized into higher layers of abstraction and queried in a coarse-to-fine manner. HiAgent (Hu et al., 2025a) manages long-horizon tasks through a subgoal-centered hierarchical working memory, keeping detailed trajectories for the currently active subgoal while compressing completed subgoals into higher-level summaries that can be selectively retrieved when needed. GraphRAG (Edge et al., 2025) builds a multi-level graph index via community detection, recursively aggregating entity-level subgraphs into community-level summaries. Extending the idea of clustering memory nodes, Zep (Rasmussen et al., 2025) formalizes agent memory as a Temporal Knowledge Graph, and it similarly performs community partitioning. ILM-TR (Tang et al., 2024) employs a tree-structured, pyramidal index coupled with an Inner Loop mechanism, repeatedly querying summaries at different abstraction levels and updating a short-term memory buffer until the retrieved evidence and generated answer stabilize. To ensure controllable personalization, EMG-RAG (Wang et al., 2024l) organizes an Editable Memory Graph into three tiers, where a tree-like type and subclass index (L1, L2) sits above an entity-level memory graph (L3). In multi-agent systems, G-Memory (Zhang et al., 2025c) structures shared experience using a three-tier graph hierarchy of insight, query, and interaction graphs. This design enables query-centric traversal to move vertically between high-level cross-trial insights and compact trajectories of concrete collaborations.

**Multi-Layer** These forms instead emphasize layered specialization, organizing memory into distinct modules or levels that focus on particular information types or functions. Lyfe Agents (Kaiya et al., 2023) separates salient long-term records from low-value transient details, allowing the system to maintain a compact, behaviorally important layer of memories. H-Mem (Sun and Zeng, 2025) explicitly arranges long-termdialogue memory into a multi-level hierarchy ordered by semantic abstraction, where lower layers store fine-grained interaction snippets and higher layers store increasingly compressed summaries. Biologically inspired architectures such as HippoRAG (Gutiérrez et al., 2024) factor memory into an associative indexing component, implemented as an open knowledge graph, and an underlying passage store, using the graph layer to orchestrate multi-hop retrieval over stored content. Its successor, HippoRAG 2 (Gutiérrez et al., 2025), extends this design into a non-parametric continual-learning setting, enriching the indexing layer with deeper passage integration and online LLM filtering. AriGraph (Anokhin et al., 2024) separates memory by information type within a unified graph, combining a semantic knowledge-graph world model that encodes environment structure with an event-level component that links concrete observations back to the semantic backbone. Similarly, SGMem (Wu et al., 2025h) adds a sentence-graph memory level on top of raw dialogue, representing histories as sentence-level graphs within chunked units. CAM (Li et al., 2025g) layers the reading process itself by incrementally clustering overlapping semantic graphs into a hierarchical schemata structure. Recently, methods such as CompassMem (Hu et al., 2026b) and MAGMA (Jiang et al., 2026) have begun exploring hierarchical composition strategies enriched with logical relations, aiming to make memory retrieval and utilization more efficient and comprehensive, so that memory can provide models with benefits beyond mere semantic information.

**Discussion** By placing memory nodes at the intersection of hierarchical and relational dimensions, Hierarchical Memory allows different memories to interact and form multi-dimensional synergies. This design helps the system encode knowledge that is more holistic and more deeply contextualized. The form also supports powerful retrieval: it enables complex, multi-path queries that move through relational networks within each layer and across abstraction levels between layers. This ability allows the system to retrieve task-relevant memories with high precision, leading to strong task performance. However, the structure’s complexity and its dense information organization create challenges for both retrieval efficiency and overall effectiveness. In particular, ensuring that all stored memories remain semantically meaningful and designing the optimal three-dimensional layout of the system remain difficult and critical problems.

## 3.2 Parametric Memory

In contrast to token-level memory, which stores information as visible and editable discrete units, parametric memory stores information directly in the model’s parameters. In this section, we examine methods that embed memory into learnable parameter spaces, allowing the model to internalize and recall information without referring to external storage.

Based on where the memory is stored relative to the core model parameters, we distinguish two primary forms of parametric memory:

### Two Major Types of Parametric Memory

1. 1. **Internal Parametric Memory:** Memory encoded within the original parameters of the model (e.g., weights, biases). These methods directly adjust the base model to incorporate new knowledge or behavior.
2. 2. **External Parametric Memory:** Memory stored in additional or auxiliary parameter sets, such as adapters, LoRA modules, or lightweight proxy models. These methods introduce new parameters to carry memory without modifying the original model weights.

This distinction reflects a key design choice: whether memory is fully absorbed into the base model or attached modularly alongside it. In the subsections that follow, for each form we outline the implementation methods, analyze its strengths and limitations, and list representative systems or work. Table 2 provides an overview of representative parametric memory methods.

### 3.2.1 Internal Parametric Memory

Internal parameter memory injects domain knowledge, personalized knowledge, or priors required by downstream tasks into the model. We also regard enhancing the model’s long-context capability as injecting a prior.**Table 2** Taxonomy of parametric memory methods. We categorize existing works based on the *storage location* relative to the core model: **Internal Parametric Memory** embeds knowledge directly into the original weights, while **External Parametric Memory** isolates information within auxiliary parameter sets. Based on the training **phase**, we performed a secondary classification of the articles. Methods are compared across three technical dimensions: (1) **Type** defines the nature of the memory, (2) **Task** specifies the target downstream application, and (3) **Optimization** denotes the optimization strategy, such as *SFT*, *FT* (fine-tuning), and *PE* (prompt engineering).

<table border="1">
<thead>
<tr>
<th>Method</th>
<th>Type</th>
<th>Task</th>
<th>Optimization</th>
</tr>
</thead>
<tbody>
<tr>
<td colspan="4" style="text-align: center;"><i>I. Internal Parametric Memory</i></td>
</tr>
<tr>
<td colspan="4"><b>(a) Pre-Train Phase</b></td>
</tr>
<tr>
<td>TNL (Qin et al., 2024b)</td>
<td>Working</td>
<td>QA, Reasoning</td>
<td>SFT</td>
</tr>
<tr>
<td>StreamingLLM (Xiao et al., 2024)</td>
<td>Working</td>
<td>QA, Reasoning</td>
<td>SFT</td>
</tr>
<tr>
<td>LMLM (Zhao et al., 2025b)</td>
<td>Factual</td>
<td>QA, Factual Gen</td>
<td>SFT</td>
</tr>
<tr>
<td>HierMemLM (Pouransari et al., 2025)</td>
<td>Factual</td>
<td>QA, Language Modeling</td>
<td>SFT</td>
</tr>
<tr>
<td>Function Token (Zhang et al., 2025o)</td>
<td>Factual</td>
<td>Language Modeling</td>
<td>Pretrain</td>
</tr>
<tr>
<td colspan="4"><b>(b) Mid-Train Phase</b></td>
</tr>
<tr>
<td>Agent-Founder (Su et al., 2025)</td>
<td>Experiential</td>
<td>Tool Calling, Deep Research</td>
<td>SFT</td>
</tr>
<tr>
<td>Early Experience (Zhang et al., 2025k)</td>
<td>Experiential</td>
<td>Tool Calling, Embodied Simulation, Reasoning, Web</td>
<td>SFT</td>
</tr>
<tr>
<td colspan="4"><b>(c) Post-Train Phase</b></td>
</tr>
<tr>
<td>Character-LM (Shao et al., 2023)</td>
<td>Factual</td>
<td>Role Playing</td>
<td>SFT</td>
</tr>
<tr>
<td>CharacterGLM (Zhou et al., 2024a)</td>
<td>Factual</td>
<td>Role Playing</td>
<td>SFT</td>
</tr>
<tr>
<td>SELF-PARAM (Wang et al., 2025o)</td>
<td>Factual</td>
<td>QA, Recommendation</td>
<td>KL Tuning</td>
</tr>
<tr>
<td>Room (Kim et al., 2023b)</td>
<td>Experiential</td>
<td>Embodied Task</td>
<td>RL</td>
</tr>
<tr>
<td>KnowledgeEditor (Cao et al., 2021)</td>
<td>Factual</td>
<td>QA, Fact Checking</td>
<td>FT</td>
</tr>
<tr>
<td>Mend (Mitchell et al., 2022)</td>
<td>Factual</td>
<td>QA, Fact Checking, Model Editing</td>
<td>FT</td>
</tr>
<tr>
<td>PersonalityEdit Mao et al. (2024)</td>
<td>Factual</td>
<td>QA, Model Editing</td>
<td>FT, PE</td>
</tr>
<tr>
<td>APP (Ma et al., 2024)</td>
<td>Factual</td>
<td>QA</td>
<td>FT</td>
</tr>
<tr>
<td>DINM (Wang et al., 2024c)</td>
<td>Experiential</td>
<td>QA, Detoxification</td>
<td>FT</td>
</tr>
<tr>
<td>AlphaEdit (Fang et al., 2025c)</td>
<td>Factual</td>
<td>QA</td>
<td>FT</td>
</tr>
<tr>
<td colspan="4" style="text-align: center;"><i>II. External Parametric Memory</i></td>
</tr>
<tr>
<td colspan="4"><b>(a) Adapter-based Modules</b></td>
</tr>
<tr>
<td>MLP-Memory (Wei et al., 2025d)</td>
<td>Factual</td>
<td>QA, Classification, Textual Entailment</td>
<td>SFT</td>
</tr>
<tr>
<td>K-Adapter (Wang et al., 2021)</td>
<td>Factual</td>
<td>QA, Entity Typing, Classification</td>
<td>SFT</td>
</tr>
<tr>
<td>WISE (Wang et al., 2024e)</td>
<td>Factual</td>
<td>QA, Hallucination Detection</td>
<td>SFT</td>
</tr>
<tr>
<td>ELDER (Li et al., 2025d)</td>
<td>Factual</td>
<td>Model Editing</td>
<td>SFT</td>
</tr>
<tr>
<td>T-Patcher (Huang et al., 2023)</td>
<td>Factual</td>
<td>QA</td>
<td>FT</td>
</tr>
<tr>
<td>Sparse Memory FT (Lin et al., 2025a)</td>
<td>Factual</td>
<td>QA</td>
<td>SFT</td>
</tr>
<tr>
<td>Memory Decoder (Cao et al., 2025a)</td>
<td>Factual</td>
<td>QA, Language Modeling</td>
<td>SFT</td>
</tr>
<tr>
<td>MemLoRA (Bini et al., 2025)</td>
<td>Factual</td>
<td>QA</td>
<td>SFT</td>
</tr>
<tr>
<td colspan="4"><b>(b) Auxiliary LM-based Modules</b></td>
</tr>
<tr>
<td>MAC (Tack et al., 2024)</td>
<td>Factual</td>
<td>QA</td>
<td>SFT</td>
</tr>
<tr>
<td>Retroformer (Yao et al., 2024a)</td>
<td>Experiential</td>
<td>QA, Web Navigation</td>
<td>RL</td>
</tr>
</tbody>
</table>

The timing of memory injection can be the pre-training phase, continued pre-training phase, mid-training phase, or post-training phase. The memory stored in internal parameters does not add extra parameters or additional modules.**Pre-Train** Some works introduce memory mechanisms during the pre-training phase, aiming to address the issue that long-tail world knowledge is difficult to compress into the limited model parameters. LMLM (Zhao et al., 2025b) and HierMemLM (Pouransari et al., 2025) store the memory for knowledge retrieval in the model during the pre-training phase, while storing the knowledge itself in an external knowledge base. Some works also optimize the computational efficiency of attention to enhance long-window memory capability (Xiao et al., 2024; Qin et al., 2024b,c; Dao, 2024; Shah et al., 2024).

**Mid-Train** During the continued pre-training phase, some works incorporate generalizable experience from downstream tasks. For instance, Su et al. (2025) and Zhang et al. (2025k) integrate agent experience. Some works improve the long-window performance or efficiency of LLMs during the mid-training phase, enabling the model to maintain more short-term memory with longer windows in memory-aided tasks (Zaheer et al., 2020; Chen et al., 2024a).

**Post-Train** Other works incorporate memory during the post-training phase to adapt to downstream tasks. Some works enable LLMs to memorize personalized user history or styles. Some works allow LLMs to learn from the successes or failures of past similar task executions. Character-LM (Shao et al., 2023) and CharacterGLM (Zhou et al., 2024a) fine-tunes the LLM into different characteristics. During the post-training phase, SELF-PARAM (Wang et al., 2025o) injects additional knowledge through KL divergence distillation without requiring extra parameters. Room (Kim et al., 2023b) stores knowledge externally while save experience internally. KnowledgeEditor (Cao et al., 2021) modifies internal parameters, aiming to alter only the knowledge that requires editing. MEND (Mitchell et al., 2022) achieves fast knowledge editing by using small networks to modify the gradients of large models. PersonalityEdit (Mao et al., 2024) proposes an LLM personality editing dataset based on personality theories in psychology. APP (Ma et al., 2024) employs multiple training objectives to ensure that adjacent knowledge is minimally disturbed during knowledge editing. DINM (Wang et al., 2024c) proposes a model editing method that enables the model to learn to reject such dangerous requests without affecting its normal functions.

**Discussion** The advantages of internal parameters lie in their simple structure, which does not add extra inference overhead or deployment costs to the vanilla model. Their drawback is the difficulty in updating internal parameters: storing new memory requires retraining, which is costly and prone to forgetting old memory. Therefore, internal parameter memory is more suitable for large-scale storage of domain knowledge or task priors, rather than short segments of personalized memory or working memory.

### 3.2.2 External Parametric Memory

Storing memory as tokens outside LLMs leads to insufficient understanding of token-form memory content in the input window by the model. Meanwhile, storing memory in the parameters of LLMs has issues, such as difficulty in updating and conflicts with pre-trained knowledge. Some works adopt a compromise approach, which **introduces memory through external parameters** without altering the original parameters of LLMs.

**Adapter** A common line of external parametric memory methods relies on modules that are attached to a frozen base model. MLP-Memory (Wei et al., 2025d) integrates RAG knowledge with Transformer decoders through MLP. K-Adapter (Wang et al., 2021) injects new knowledge by training task-specific adapter modules while keeping the original backbone unchanged, enabling continual knowledge expansion without interfering with pre-trained representations. WISE (Wang et al., 2024e) further introduces a dual-parameter memory setup—separating pre-trained knowledge and edited knowledge—and a routing mechanism that dynamically selects which parameter memory to use at inference time, thus mitigating conflicts during lifelong editing. ELDER (Li et al., 2025d) advances this direction by maintaining multiple LoRA modules and learning a routing function that adaptively selects or blends them based on input semantics, improving robustness and scalability in long-term editing scenarios. Collectively, these methods leverage additional parameter subspaces to store and retrieve memory in a modular and reversible manner, avoiding the risks of catastrophic interference associated with directly modifying the core model weights.The diagram illustrates the integration of latent memory into LLM agents, categorized into three main approaches:

- **(a) Generate:** Auxiliary Models (SLM, LoRA, Decoder heads) produce Latent Emb. (Latent Embeddings) which then interfere or augment the LLM's forward pass.
- **(b) Reuse:** KV cache/Intermediate Embeddings are used to directly propagate prior computational states, augmenting the LLM's forward pass.
- **(c) Transform:** Token Selection, Token Merge, and Token Projection are used to compress internal states, augmenting the LLM's forward pass.

The central process is the **LLM Internal Calculation**, which includes the **LLM Forward** and **Layer-wise transformer forward** steps. The **Input Query** is fed into the **LLM Forward** step, which also receives **Inspection** and **Closer** inputs. The **Output** is generated from the **Layer-wise transformer forward** step. The **LLM Internal Calculation** is enclosed in a dashed box, and the **Layer-wise transformer forward** step is also enclosed in a dashed box.

**Figure 4** Overview of Latent Memory integration in LLM agents. Unlike explicit text storage, latent memory operates within the model’s internal representational space. The framework is categorized by the origin of the latent state: (a) **Generate**, where auxiliary models synthesize embeddings to interfere with or augment the LLM’s forward pass; (b) **Reuse**, which directly propagates prior computational states such as KV caches or intermediate embeddings; and (c) **Transform**, which compresses internal states through token selection, merging, or projection to maintain efficient context.

**Auxiliary LM** Beyond Adapter-based storage, another line of work adopts a more architecturally decoupled form of external parametric memory, where memory is stored in a separate model or external knowledge module. MAC (Tack et al., 2024) compresses the information from a new document into a compact modulation through an amortization network, and stores it in a memory bank. Retroformer (Yao et al., 2024a) proposes a learning paradigm for memorizing the experiences of successes or failures in past task executions.

**Discussion** This external parametric memory approach provides a balance between adaptability and model stability. Because memory is encoded into additional parameter modules, it can be added, removed, or replaced without interfering with the base model’s pre-trained representation space. This supports modular updates, task-specific personalization, and controlled rollback, while avoiding the catastrophic forgetting or global weight distortion that may occur in full model fine-tuning.

However, this approach also comes with limitations. External parameter modules must still integrate with the model’s internal representation flow, meaning that their influence is indirect and mediated through the model’s attention and computation pathways. As a result, the effectiveness of memory injection depends on how well the external parameters can interface with internal parametric knowledge.### 3.3 Latent Memory

#### Definition of Latent Memory

Latent memory refers to memory that is carried implicitly in the model’s internal representations (e.g., KV cache, activations, hidden states, latent embeddings), rather than being stored as explicit, human-readable tokens or dedicated parameter sets.

Latent avoids exposing memory in plaintext and introduces practically less inference latency, while potentially offering better performance gains by preserving fine-grained contextual signals within the model’s own representational space.

As shown in Figure 4, we organize prior work by the origin of latent memory, which means how the latent state is formed and introduced into the agent. We summarize the works in this part in Table 3.

#### Three Major Types of Latent Memory

1. 1. **Generate:** latent memory is produced by an independent model or a module, and then supplied to the agent as reusable internal representations.
2. 2. **Reuse:** latent memory is directly carried over from prior computation, most prominently KV-cache reuse (within or across turns), as well as recurrent or stateful controllers that propagate hidden states.
3. 3. **Transform:** existing latent state is transformed into new representations (e.g., distillation, pooling, or compression), so the agent can retain essentials while reducing latency and context footprint.

#### 3.3.1 Generate

A major line of work builds memory by **generating new latent representations** rather than reusing or transforming existing activations. In this paradigm, the model or an auxiliary encoder creates compact continuous states. These states may appear as special tokens in the sequence or as standalone vectors. They summarize the essential information from long contexts, task trajectories, or multimodal inputs. The generated latent summaries are then stored, inserted, or used as conditions for later reasoning or decision-making. This enables the system to operate beyond its native context length, maintain task-specific intermediate states, and retain knowledge across episodes without revisiting the original input. Although the concrete forms vary across studies, the underlying idea remains consistent. Memory is explicitly produced through learned encoding or compression, and the resulting latent states serve as reusable memory units that support future inference.

This design choice may also raise potential ambiguity with parametric memory, particularly since many methods rely on separately trained models to generate latent representations. In this chapter, however, our classification is grounded in the form of memory rather than the learning mechanism. Crucially, although these approaches generate memory through learned encoding, the produced latent representations are explicitly instantiated and reused as independent memory units, rather than being directly embedded into the model’s parameters or forward-pass activations. We will return to this distinction when discussing individual methods in detail.

**Single Modal** In the single-modal setting, a major group of methods focuses on long-context processing and language modeling, where models generate a small set of internal representations to replace long raw inputs (Mu et al., 2023; Luo et al., 2024; Xu et al., 2025d; Chevalier et al., 2023; Qian et al., 2025; Wang et al., 2024j, 2025n). A typical strategy is to compress long sequences into a few internal tokens or continuous vectors that can be reused during later inference. For example, Gist (Mu et al., 2023) train a language model to produce a set of gist tokens after processing a long prompt. Luo et al. (2024) introduce a special sentinel token at each chunk boundary and encourage the model to aggregate local semantics into that token. SoftCoT (Xu et al., 2025d) follows a similar direction by generating instance-specific soft tokens from the last hidden state.**Table 3** Taxonomy of latent memory methods. We categorize existing works based on the *origin* of the latent state: **Generate** synthesizes memory via auxiliary modules, **Reuse** propagates internal computational states, and **Transform** compresses, modifies or restructs existing latent state. Methods are compared across three technical dimensions: (1) **Form** specifies the specific data type of the latent memory, (2) **Type** defines the nature of the recorded content (e.g., Working, Factual, and Experiential), and (3) **Task** denotes the target downstream application.

<table border="1">
<thead>
<tr>
<th>Method</th>
<th>Form</th>
<th>Type</th>
<th>Task</th>
</tr>
</thead>
<tbody>
<tr>
<td colspan="4" style="text-align: center;"><i>I. Generate</i></td>
</tr>
<tr>
<td colspan="4"><b>(a) Single Modal</b></td>
</tr>
<tr>
<td>Gist (Mu et al., 2023)</td>
<td>Gist Tokens</td>
<td>Working</td>
<td>Long-context Compression</td>
</tr>
<tr>
<td>Taking a Deep Breath (Luo et al., 2024)</td>
<td>Sentinel Tokens</td>
<td>Working</td>
<td>Long-context QA</td>
</tr>
<tr>
<td>SoftCoT (Xu et al., 2025d)</td>
<td>Soft Tokens</td>
<td>Working</td>
<td>Reasoning</td>
</tr>
<tr>
<td>CARE (Choi et al., 2025)</td>
<td>Memory Tokens</td>
<td>Working</td>
<td>QA, Fact Checking</td>
</tr>
<tr>
<td>AutoCompressor (Chevalier et al., 2023)</td>
<td>Summary Vectors</td>
<td>Working</td>
<td>QA, Compression</td>
</tr>
<tr>
<td>MemoRAG (Qian et al., 2025)</td>
<td>Global Semantic States</td>
<td>Working</td>
<td>QA, Summary</td>
</tr>
<tr>
<td>MemoryLLM (Wang et al., 2024j)</td>
<td>Persistent Tokens</td>
<td>Factual</td>
<td>Long-conv QA, Model Editing</td>
</tr>
<tr>
<td>M+ (Wang et al., 2025n)</td>
<td>Cross-layer Token Pools</td>
<td>Factual</td>
<td>QA</td>
</tr>
<tr>
<td>LM2 (Kang et al., 2025b)</td>
<td>Matrix Slots</td>
<td>Working</td>
<td>QA, Reasoning</td>
</tr>
<tr>
<td>Titans (Behrouz et al., 2025b)</td>
<td>Neural Weights (MLP)</td>
<td>Working</td>
<td>QA, Language Modeling</td>
</tr>
<tr>
<td>MemGen (Zhang et al., 2025d)</td>
<td>LoRA Fragments</td>
<td>Working, Exp.</td>
<td>QA, Math, Code, Embodied Task, Reasoning</td>
</tr>
<tr>
<td>EMU (Na et al., 2024)</td>
<td>Embeddings w/ Returns</td>
<td>Factual</td>
<td>Game</td>
</tr>
<tr>
<td>TokMem (Wu et al., 2025j)</td>
<td>Memory Tokens</td>
<td>Exp.</td>
<td>Funcation calling</td>
</tr>
<tr>
<td>Nested Learning (Behrouz et al., 2025a)</td>
<td>Nested Optimization</td>
<td>Factual</td>
<td>Language Modeling</td>
</tr>
<tr>
<td>Memoria (Park and Bak, 2024)</td>
<td>Three memory layers with engrams</td>
<td>Factual</td>
<td>Language Modeling</td>
</tr>
<tr>
<td colspan="4"><b>(b) Multi-Modal</b></td>
</tr>
<tr>
<td>CoMem (Wu et al., 2025d)</td>
<td>Multimodal Embeddings</td>
<td>Factual</td>
<td>Multimodal QA</td>
</tr>
<tr>
<td>ACM (Wu et al., 2025e)</td>
<td>Trajectory Embeddings</td>
<td>Working</td>
<td>Web</td>
</tr>
<tr>
<td>Time-VLM (Zhong et al., 2025)</td>
<td>Patch Embeddings</td>
<td>Working</td>
<td>Video Understanding</td>
</tr>
<tr>
<td>Mem Augmented RL (Mezghani et al., 2022)</td>
<td>Novelty State Encoder</td>
<td>Working</td>
<td>Visual Navigation</td>
</tr>
<tr>
<td>MemoryVLA (Shi et al., 2025a)</td>
<td>Perceptual States</td>
<td>Factual, Working</td>
<td>Embodied Task</td>
</tr>
<tr>
<td>XMEm (Cheng and Schwing, 2022)</td>
<td>Key-Value Embeddings</td>
<td>Working</td>
<td>Video Segmentation</td>
</tr>
<tr>
<td colspan="4" style="text-align: center;"><i>II. Reuse</i></td>
</tr>
<tr>
<td>Memorizing Transformers (Wu et al., 2022)</td>
<td>External KV Cache</td>
<td>Working</td>
<td>Language Modeling</td>
</tr>
<tr>
<td>SirLLM (Yao et al., 2024b)</td>
<td>Entropy-selected KV</td>
<td>Factual</td>
<td>Long-conv QA</td>
</tr>
<tr>
<td>Memory<sup>3</sup> (Yang et al., 2024a)</td>
<td>Critical KV Pairs</td>
<td>Factual</td>
<td>QA</td>
</tr>
<tr>
<td>FOT (Tworkowski et al., 2023)</td>
<td>Memory-Attention KV</td>
<td>Working</td>
<td>QA, Few-shot learning, Language Modeling</td>
</tr>
<tr>
<td>LONGMEM (Wang et al., 2023b)</td>
<td>Residual SideNet KV</td>
<td>Working</td>
<td>Language Modeling and Understanding</td>
</tr>
<tr>
<td colspan="4" style="text-align: center;"><i>III. Transform</i></td>
</tr>
<tr>
<td>Scissorhands (Liu et al., 2023b)</td>
<td>Pruned KV</td>
<td>Working</td>
<td>Image classification &amp; generation</td>
</tr>
<tr>
<td>SnapKV (Li et al., 2024b)</td>
<td>Aggregated Prefix KV</td>
<td>Working</td>
<td>Language Modeling</td>
</tr>
<tr>
<td>PyramidKV (Cai et al., 2024)</td>
<td>Layer-wise Budget</td>
<td>Working</td>
<td>Language Modeling</td>
</tr>
<tr>
<td>RazorAttention (Tang et al., 2025a)</td>
<td>Compensated Window</td>
<td>Working</td>
<td>Language Modeling</td>
</tr>
<tr>
<td>H2O (Zhang et al., 2023)</td>
<td>Heavy Hitter Tokens</td>
<td>Working</td>
<td>QA, Language Modeling</td>
</tr>
<tr>
<td>R<sup>3</sup>Mem (Wang et al., 2025k)</td>
<td>Virtual memory tokens with reversible compression</td>
<td>Working</td>
<td>QA, Language Modeling</td>
</tr>
</tbody>
</table>

CARE (Choi et al., 2025) further extends the latent tokens by training a context assessor that compresses retrieved RAG documents into compact memory tokens.

Work such as AutoCompressor (Chevalier et al., 2023) and MemoRAG (Qian et al., 2025) emphasizes vectorized or standalone latent representations. AutoCompressor (Chevalier et al., 2023) encodes entire long documents into a small number of summary vectors serving as soft prompts, while MemoRAG (Qian et al., 2025) uses an LLM to produce compact hidden-state memories capturing global semantic structure. These approaches not only abstract away from raw text but also transform retrieved or contextualized information into new latent memory units optimized for reuse. To support more persistent memory, MemoryLLM (Wang et al., 2024j) embeds a set of dedicated memory tokens within the model’s latent space. M+ (Wang et al., 2025n) extends this idea into a cross-layer long-term memory architecture. LM2 (Kang et al., 2025b) follows a related but structurally distinct direction by introducing matrix-shaped latent memory slots into every layer.

A different branch of work internalizes the generation of latent memory within the model’s parameter dynamics. Although these works rely on parameterized modules, their operational memory units remain latent representations, placing them firmly within this category. Titans (Behrouz et al., 2025b) compresses long-range information into an online-updated MLP weight, producing latent vectors during inference. MemGen (Zhang et al., 2025d) dynamically generates latent memory during decoding: two LoRA adapters determine where to insert memory fragments and what latent content to insert. EMU (Na et al., 2024) trains a state encoder to produce latent embeddings annotated with returns and desirability.

**Multi Modal** In multimodal settings, generative latent memory extends to images, audios and videos, encoding them as compact latent representations. CoMem (Wu et al., 2025d) uses a VLM to compressmultimodal knowledge into a set of embeddings that act as plug-and-play memory. Similarly, [Wu et al. \(2025e\)](#) compresses entire GUI interaction trajectories into fixed-length embeddings and injects them into the VLM input space. For temporal modeling, Time-VLM ([Zhong et al., 2025](#)) divides video or interaction streams into patches and generates a latent embedding for each patch.

In vision-based navigation, [Mezghani et al. \(2022\)](#) learns a state encoder that maps visual observations into a latent space and constructs an episodic memory containing only novel observations. MemoryVLA ([Shi et al., 2025a](#)) maintains a Perceptual–Cognitive Memory Bank that stores both perceptual details and high-level semantics as transformer hidden states. In long-video object segmentation, XMem([Cheng and Schwing, 2022](#)) encodes each frame into key–value latent embeddings and organizes them into a multi-stage memory comprising perceptual, working, and long-term components.

**Discussion** These single-modal and multimodal approaches share the same fundamental principle: first generate compact latent representations, then maintain and retrieve them as memory entries. The model can actively construct highly information-dense representations tailored to the task, capturing key dynamics, long-range dependencies, or cross-modal relations with minimal storage cost. It also avoids repeatedly processing the full context, enabling more efficient reasoning across extended interactions.

However, the drawbacks are equally evident. The generation process itself may introduce information loss or bias, and the states can drift or accumulate errors over multiple read–write cycles. Moreover, training a dedicated module to generate latent representations introduces additional computational overhead, data requirements, and engineering complexity.

### 3.3.2 Reuse

In contrast to methods that generate new latent representations, another line of work directly **reuses the model’s internal activations, primarily the key–value (KV) cache**, as latent memory. These approaches do not transform(modify, compress) the stored KV pairs and instead treat the raw activations from forward passes as reusable memory entries. The main challenge is to determine which KV pairs to keep, how to index them, and how to retrieve them efficiently under long-context or continual-processing demands.

From a cognitive perspective, [Gershman et al. \(2025\)](#) provides conceptual grounding by framing biological memory as a key–value system, where keys function as retrieval addresses and values encode stored content—an abstraction closely aligned with KV-based memory in modern LLMs. Memorizing Transformers ([Wu et al., 2022](#)) explicitly store past KV pairs and retrieve them via K-nearest-neighbor search during inference. FOT ([Tworkowski et al., 2023](#)) extends this line of work by introducing memory-attention layers that perform KNN-based retrieval over additional KV memories during inference. LONGMEM ([Wang et al., 2023b](#)) similarly augments long-range retrieval, employing a lightweight residual SideNet that treats historical KV embeddings as a persistent memory store. These systems demonstrate how retrieval-aware organization of latent KV states can substantially enhance access to distant information.

**Discussion** Reuse-type latent memory methods highlight the effectiveness of directly leveraging the model’s own internal activations as memory, showing that carefully curated KV representations can serve as a powerful and efficient substrate for long-range retrieval and reasoning.

Their greatest strength lies in preserving the full fidelity of the model’s internal activations, ensuring that no information is lost through pruning or compression. This makes them conceptually simple, easy to integrate into existing forms, and highly faithful to the model’s original computation. However, raw KV caches grow rapidly with context length, which increases memory consumption and can make retrieval less efficient. The effectiveness of reuse therefore depends heavily on indexing strategies.

### 3.3.3 Transform

Transform-type latent memory methods focus on **modifying, compressing, or restructuring existing latent states** rather than generating entirely new ones or directly reusing raw KV caches. These approaches treat KV caches and hidden activations as malleable memory units, reshaping them through selection, aggregation, or structural transformation. In doing so, they occupy a conceptual middle ground between generate-type and**Token-level Memory**

**Features:**

- - Symbolic, addressable, transparent
- - Swift adding/deleting/updating

**Suitable Applications:**

- Multi-turn chatbot
- Personalized agents
- Recommender system
- High-stake domains (law, finance, medical)

**Parametric Memory**

**Features:**

- - Implicit, abstract, and generalizable
- - Slower memory update
- - (Typically) better performance gain
- - More severe catastrophic forgetting

**Suitable Applications:**

- Role-playing
- Reasoning-intensive task
- Tasks that require fundamentally new capabilities

**Latent Memory**

**Features:**

- - Implicit, human-unreadable
- - Trade-off between efficiency/flexibility
- - Convenient modality fusion
- - Machine-native and token-efficient

**Suitable Applications:**

- Multimodal memory
- On-device or edge deploy
- Low-resource or small-data setting

**Figure 5** Overview of three complementary memory paradigms for LLM agents. Token-level, parametric, and latent memories differ in their representational form, update dynamics, interpretability, and efficiency, leading to distinct strengths, limitations, and application domains in long-horizon and interactive agent systems.

reuse-type memory: the model does not create fresh latent representations, but it also does more than simply replay stored KV pairs.

A major line of work focuses on compressing KV caches while preserving essential semantics. Some methods reduce memory usage by keeping only the most influential tokens. Scissorhands (Liu et al., 2023b) prunes tokens based on attention scores when cache capacity is exceeded, whereas SnapKV (Li et al., 2024b) aggregates high-importance prefix KV representations via a head-wise voting mechanism. PyramidKV (Cai et al., 2024) reallocates KV budgets across layers. SirLLM (Yao et al., 2024b) builds on this perspective by estimating token importance with a token-entropy criterion and selectively retaining only informative KV entries. Memory<sup>3</sup> (Yang et al., 2024a) only stores the most critical attention key-value pairs, significantly shrinking storage requirements. RazorAttention (Tang et al., 2025a) introduces a more explicit compression scheme: it computes the effective attention span of each head, retains only a limited local window, and uses compensation tokens to preserve information from discarded entries. From a more efficiency-oriented perspective, H2O (Zhang et al., 2023) adopts a simpler eviction strategy, retaining only the most recent tokens along with special H2 tokens to reduce memory footprint.

**Discussion** These methods demonstrate how latent memory can be transformed, through selection, retrieval enhancement, or compressed re-encoding, into more effective memory representations, enabling LLMs to extend their usable context length and improve reasoning performance without relying on raw cache reuse.

Their main advantage lies in producing more compact and information-dense memory representations, which reduce storage cost and enable efficient retrieval over long contexts. By reshaping latent states, these methods allow the model to access distilled semantic signals that may be more useful than raw activations. However, transformation introduces the risk of information loss, and the compressed states can become harder to interpret or verify compared with directly reused KV caches. The additional computation required for pruning, aggregation, or re-encoding also increases system complexity.### 3.4 Adaptation

As shown above, such a large body of work has focused on agent memory, clearly demonstrating that memory mechanisms are essential for agent systems (Zhang et al., 2025s). The choice of memory type in an agent system reflects how designers expect the agent to behave in a given task. Designers are not simply asking the agent to remember certain information, but also implicitly expressing how they want that information to shape the agent’s behavior. Therefore, choosing the right type of memory for a task is far more than a simple combinatorial choice.

In this section, we start from the features of each memory type and discuss which tasks and scenarios they are best suited for in an ideal setting, as shown in Figure 5. We hope this discussion can offer useful ideas and guidance for making practical choices. The examples illustrate only one possible form of memory in these idealized settings and do not imply that other memory types lack unique advantages in the same scenarios.

**Token-level Memory** Token-level memory remains *symbolic*, *addressable*, and *transparent*, making it particularly well suited for scenarios where explicit reasoning, controllability, and accountability are essential. This type of memory excels in real-time, high-frequency update settings, where an agent must continuously track and revise information, and where the knowledge itself exhibits a clear structure that can be explicitly modeled. Its externalizability allows memory to be easily inspected, audited, transferred, or revised, making it especially suitable for domains requiring precise add/delete/update operations. The high level of interpretability further ensures that an agent’s decision process can be traced back to concrete memory units, a crucial property in high-stakes applications. Moreover, token-level memory provides long-term stability and avoids catastrophic forgetting, enabling agents to accumulate reliable knowledge over extended time horizons. Another practical advantage is that token-level memory is often implemented as a plug-and-play module, allowing it to be readily integrated with the latest closed-source or open-source foundation models without modifying their internal parameters.

#### Possible Scenarios:

- • Chatbots and multi-turn dialogue systems. (Zhong et al., 2024; Lu et al., 2023; Chhikara et al., 2025)
- • Long-horizon or life-long agents requiring stable memory. (Wang et al., 2024f; Westhäuser et al., 2025)
- • User-specific personalization profiles. (Wang et al., 2024f; Lee et al., 2023)
- • Recommendation systems. (Wang et al., 2024h; Huang et al., 2025d; Xi et al., 2024a)
- • Enterprise or organizational knowledge bases.
- • Legal, compliance, and other high-stakes domains requiring verifiable provenance.

**Parametric Memory** Compared with symbolic memory, parametric memory is *implicit*, *abstract*, and *generalizable*, making it naturally suited to tasks requiring conceptual understanding and broad pattern induction. It is particularly effective when the agent must rely on general knowledge or rules that apply across diverse contexts, because such regularities can be internalized as distributed representations without requiring explicit external lookup. This internalization supports fluid reasoning and end-to-end processing, enabling the model to generalize systematically to unseen tasks or problem variations. Consequently, parametric memory is better aligned with tasks demanding structural insight, robust abstraction, and deeply ingrained behavioral or stylistic patterns.

#### Possible Scenarios:

- • Role-playing or persona-consistent behaviors. (Shao et al., 2023; Zhou et al., 2024a)
- • Mathematical reasoning, coding, games, and structured problem-solving.
- • Human alignment and normative behavioral priors.
- • Stylized, professional, or domain-expert responses.
