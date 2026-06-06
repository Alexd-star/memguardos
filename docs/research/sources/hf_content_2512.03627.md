Title: MemVerse: Multimodal Memory for Lifelong Learning Agents

URL Source: https://arxiv.org/html/2512.03627

Markdown Content:
Junming Liu, Yifei Sun 1 1 footnotemark: 1, Weihua Cheng, Haodong Lei, Yirong Chen, Licheng Wen, Xuemeng Yang, 

Daocheng Fu, Pinlong Cai, Nianchen Deng, Yi Yu, Shuyue Hu, Botian Shi, Ding Wang 

Shanghai Artificial Intelligence Laboratory 

liujunming@pjlab.org.cn sunyifei@pjlab.org.cn wangding@pjlab.org.cn 

![Image 1: [Uncaptioned image]](https://arxiv.org/html/2512.03627v1/x1.png)[https://github.com/KnowledgeXLab/MemVerse](https://github.com/KnowledgeXLab/MemVerse)

###### Abstract

Despite rapid progress in large-scale language and vision models, AI agents still suffer from a fundamental limitation: they cannot remember. Without reliable memory, agents catastrophically forget past experiences, struggle with long-horizon reasoning, and fail to operate coherently in multimodal or interactive environments. We introduce MemVerse, a model-agnostic, plug-and-play memory framework that bridges fast parametric recall with hierarchical retrieval-based memory, enabling scalable and adaptive multimodal intelligence. MemVerse maintains short-term memory for recent context while transforming raw multimodal experiences into structured long-term memories organized as hierarchical knowledge graphs. This design supports continual consolidation, adaptive forgetting, and bounded memory growth. To handle real-time demands, MemVerse introduces a periodic distillation mechanism that compresses essential knowledge from long-term memory into the parametric model, allowing fast, differentiable recall while preserving interpretability. Extensive experiments demonstrate that MemVerse significantly improves multimodal reasoning and continual learning efficiency, empowering agents to remember, adapt, and reason coherently across extended interactions.

1 Introduction
--------------

Despite remarkable progress in large-scale language and vision models, AI agents remain fundamentally constrained by their lack of memory [Zhang_2025_Survey], particularly in visual reasoning [Dong_2025_Insight-V], robotic interaction [Chen_2025_VidBot], and multimodal scene understanding [Yu_2025_Inst3D-LMM]. Memory is essential for enabling agents to adapt to dynamic environments, integrate new experiences with prior knowledge, and perform long-horizon reasoning. Unlike humans, whose intelligence emerges from the continuous accumulation and abstraction of multimodal experiences [Deary2010], current AI agents operate in a largely stateless manner, treating each task as independent [Liu_2025_VaLiK, Wang_2025_MIRIX]. Consequently, developing a robust mechanism to organize multimodal experiences is a fundamental step in agent design, especially as AI applications shift from single-task inference to continuous interaction in real-world settings.

Current memory solutions for AI agents fall into two dominant paradigms, both inadequate for multimodal [Xiao2025VisualIP, Chhikara_2025_Mem0, Wang_2025_MIRIX] and lifelong learning [Wu_2022_MeMViT, Fan_2024_VideoAgent] scenarios. The first is parameter-embedded memory, where knowledge is encoded directly into model weights through fine-tuning, prompt tuning, or in-context learning [pritzel2017nec, Tai_2017_MemNet]. While this approach leverages the model’s inherent capacity for compression and associative recall, it suffers from rigidity: memory capacity is bounded by fixed parameters, updates require costly retraining, and newly acquired information often interferes with existing knowledge. Moreover, such parameter-embedded memory is inherently a black box that limits interpretability and control. The second paradigm relies on external static storage, typically implemented through Retrieval-Augmented Generation (RAG)-style systems that maintain raw interaction logs or documents[Liu_2025_HM-RAG]. Memory access is handled via embedding-based retrieval [Huang_2020_Embedding] or keyword matching [Liu_2006_Effective], but the absence of structured abstraction leads to redundancy and inefficiency. As the database grows, retrieval becomes increasingly noisy and computationally expensive, and the agent struggles to distill or generalize from accumulated experiences.

These limitations give rise to three critical and interconnected challenges that constrain the development of capable AI agents in multimodal settings. First, memory needs to be decoupled from model parameters. Parameter-embedded memory is tightly bound to model weights, making it difficult to scale or adapt [li2025frequency, Yan_2025_MemoryR1]. Agents cannot expand their memory capacity without altering the underlying model, nor can they learn new tasks without the risk of catastrophic forgetting. This rigidity fundamentally impedes lifelong learning, where agents must continuously assimilate new multimodal experiences. Second, static external memory requires structured organization and abstraction. RAG-style external storage typically logs raw interactions without abstraction, failing to transform linear, redundant data into structured, actionable knowledge [meng2025ranking]. Without mechanisms for hierarchical organization, adaptive pruning, or semantic abstraction, agents incur escalating computational costs and retrieval inefficiencies. Third, memory must support multimodal reasoning. Most existing memory systems remain text-centric, overlooking the inherently multimodal nature of real-world experience [A-MEM, yao2023react]. The inability to align or associate information across visual, linguistic, and other sensory modalities results in flat memories that lack contextual grounding and cross-modal reasoning capability—ultimately constraining agent performance in perception-rich environments.

To overcome these limitations, we introduce MemVerse, a model-agnostic memory framework designed for AI agents engaged in multimodal reasoning and lifelong learning. MemVerse provides a unified interface that integrates hierarchical retrieval-based long-term memory with a lightweight parametric memory model, enabling scalable and context-aware reasoning. Drawing inspiration from the complementary roles of fast and slow thinking in biological cognition [Kahneman2011], the framework employs a dual-path architecture. The fast pathway, implemented as the parametric memory model, provides immediate, differentiable recall by periodically distilling essential knowledge from long-term memory. The slow pathway, realized as hierarchical retrieval-based memory, continuously accumulates, abstracts, and organizes multimodal experiences into specialized memory types structured as hierarchical knowledge graphs. These include core memory, which stores user-specific knowledge; episodic memory, which logs detailed, time-ordered experiences; and semantic memory, which maintains structured, generalizable knowledge about entities and their relationships. This design allows MemVerse to consolidate knowledge efficiently and adaptively forget irrelevant information.

In summary, our work introduces MemVerse, a unified memory framework that bridges parametric and non-parametric mechanisms for multimodal agents. The key contributions are as follows:

*   •MemVerse is a model-agnostic, plug-and-play memory system that integrates hierarchical retrieval-based long-term memory with a lightweight parametric memory model, providing a unified interface for lifelong multimodal reasoning. 
*   •MemVerse transforms raw multimodal experiences into specialized memory types that are organized as hierarchical knowledge graphs. This enables continual abstraction, adaptive forgetting, and bounded memory growth, turning unstructured histories into interpretable knowledge. 
*   •We introduce a periodic distillation mechanism that compresses essential long-term memory into a lightweight parametric model, enabling fast, differentiable recall while preserving transparency and controllability. 

2 Related Work
--------------

### 2.1 Memory for LLM Agents.

Memory is a fundamental module in LLM-based agents, shaping their ability to adapt, generalize, and perform across complex tasks[Memory_Survey]. It is commonly classified into parametric and non-parametric paradigms. Parametric memory embeds knowledge directly into model parameters, either via trajectory-based fine-tuning, as in FireAct[Chen_2023_Fireact], modular fine-tuning of separate components, as in AgentLumos[Yin_2024_AgentLumos], or through latent memory pools within transformers, as in MemoryLLM[Wang_2024_MemoryLLM], enabling models to retain and update knowledge post-training. Hybrid approaches combine parametric mechanisms with algorithmic or latent memory structures to handle long contexts, such as MemAgent[Yu_2025_MemAgent], which retains high-reward episodes via multi-conversation reinforcement optimization, and MemGen[Zhang_2025_MemGen], which employs generative latent memory networks. Non-parametric memory decouples storage from the model, using external databases or memory services to provide persistent, adaptable context; examples include MemGPT[Packer_2023_MemGPT], MemoryBank[Zhong_2024_MemoryBank], and MemoRAG[Qian_2025_MemoRAG], which implement tiered retrieval, temporal relevance, and dual-system retrieval to maintain personalized and accurate long-term memory. Production-grade memory layers, such as Mem0[Chhikara_2025_Mem0] and SuperMemory[Shah_2025_SuperMemory], provide multi-level summarization, compression, and fast read/write operations for scalable retrieval-augmented deployments. Collectively, these works illustrate the evolution of agent memory from static parametric encoding to dynamic, retrieval-augmented, and generative mechanisms, while highlighting remaining challenges in real-time adaptation and rapid task switching.

### 2.2 Multimodal Knowledge Retrieval

Memory and retrieval are essential for knowledge-intensive tasks, particularly in multimodal settings. Dense retrieval methods, such as Dense Passage Retrieval (DPR)[karpukhin2020dpr], learn vector representations that often outperform sparse retrieval in open-domain question answering. Retrieval-Augmented Generation (RAG)[lewis2020rag] integrates parametric LLMs with non-parametric retrieval to improve factual accuracy and adaptivity. For multimodal retrieval, contrastive image-text pretraining methods, such as CLIP[radford2021clip], and efficient vision-language models, such as BLIP and BLIP-2[li2023blip2], enable scalable cross-modal representations for image-text retrieval and grounding. At a higher structural level, multimodal knowledge graphs organize heterogeneous signals into nodes and relations, supporting cross-modal reasoning and retrieval. Surveys and construction methods outline pipelines, verification strategies, and applications for these graphs[zhu2022mmkg, chen2024kgmm]. Integrating multimodal retrieval with LLM memory and multi-agent coordination allows agents to perform context-aware, knowledge-rich reasoning across modalities.

![Image 2: Refer to caption](https://arxiv.org/html/2512.03627v1/x2.png)

Figure 2: MemVerse integrates three memory components: short-term memory for recent conversational contexts, long-term memory structured as a multimodal knowledge graph with entities and semantic relationships, and parametric memory as a lightweight neural model for fast context encoding. A central memory orchestrator manages retrieval and storage across these components, enabling the agent to process multimodal inputs and support lifelong learning.

3 Methodology
-------------

In this section, we describe the technical design of MemVerse, illustrated in Figure 2. At the center of the architecture is the memory orchestrator, which governs all interactions between the hierarchical retrieval-based memory and the parametric memory. The orchestrator operates through rule-based control logic without introducing additional trainable parameters, and executes all memory operations, including addition, update, deletion, and retrieval, under a unified interface.

The hierarchical retrieval-based memory consists of short-term and long-term memory components. Short-term memory stores recent context and supports local consistency during ongoing interactions. Long-term memory provides persistent knowledge storage and is structured as knowledge graphs. Parallel to the retrieval-based memory, the parametric memory offers a fast, differentiable pathway for knowledge recall and generalization. It maintains compact neural representations that are periodically distilled from the long-term memory, enabling rapid access to essential information without relying solely on retrieval-based search.

### 3.1 Hierarchical Retrival-based Memory

#### Multimodal Processing.

To handle arbitrary multimodal inputs, such as images, videos, and audio, we first employ pretrained MLLMs to convert the raw data into textual representations. Specifically, each input modality M M is encoded into a sequence of descriptive tokens:

S=𝒟 text​(𝒜​(ℰ mod​(M))),S=\mathcal{D}_{\text{text}}\Big(\mathcal{A}\big(\mathcal{E}_{\text{mod}}(M)\big)\Big),(1)

where M M denotes the input modality, ℰ mod\mathcal{E}_{\text{mod}} is the modality-specific encoder that extracts features from M M, 𝒜\mathcal{A} performs cross-modal alignment or interaction, and 𝒟 text\mathcal{D}_{\text{text}} generates the textual tokens.

The resulting text S S serves as a text chunk corresponding to the original multimodal input M M. This explicit alignment allows any symbolic entity or relation derived from S S to be directly linked back to the original multimodal data [Liu_2025_VaLiK].

#### Short-Term Memory.

To avoid redundant retrieval and continuous updates to long-term storage, we introduce a short-term memory mechanism that caches recent interaction states. Specifically, given a dialogue sequence {q 1,q 2,…,q t}\{q_{1},q_{2},...,q_{t}\}, STM retains the most recent K K queries within a sliding window:

ℳ STM={q t−K+1,q t−K+2,…,q t}.\mathcal{M}_{\text{STM}}=\{q_{t-K+1},q_{t-K+2},...,q_{t}\}.(2)

Since the contextual information in a short dialogue session is already captured within this window, frequent updates to the external memory or implicit models are unnecessary. Instead, consolidation is performed periodically or when sufficient new knowledge accumulates, ensuring both efficiency and stability.

#### Long-Term Memory.

We implement LTM as a paired structure ℳ=({𝒢 k},𝒞)\mathcal{M}=(\{\mathcal{G}_{k}\},\mathcal{C}), where each 𝒢 k=(𝒱 k,ℛ k)\mathcal{G}_{k}=(\mathcal{V}_{k},\mathcal{R}_{k}) is a knowledge graph corresponding to a specific type of memory (core, episodic, or semantic), and 𝒞\mathcal{C} stores the original dialogue text chunks:

ℳ=({𝒢 k},𝒞),k∈{core, episodic, semantic}.\mathcal{M}\;=\;\Big(\;\{\mathcal{G}_{k}\}\;,\;\mathcal{C}\;\Big),\quad k\in\{\text{core, episodic, semantic}\}.(3)

Core memory stores durable, user-specific facts and preferences to support personalization; episodic memory captures time-ordered, event-based interactions as detailed entries; and semantic memory maintains abstract, generalizable knowledge about concepts and objects. Together, these subgraphs allow the system to accumulate, organize, and refine knowledge over extended time horizons.

Directly storing raw dialogue text as memory is both inefficient and prone to retrieval errors. The number of text chunks can grow rapidly, and simple vector-based retrieval may fail to capture complex multi-hop relations or the topological structure inherent in the interactions. To address this, we first compress the original dialogue text 𝒞\mathcal{C} into salient _memory descriptions_ using an LLM, capturing only the most essential information, such as key entities, relations, and critical context. These memory descriptions are then structured into a Multimodal Knowledge Graph (MMKG), which preserves relational connectivity and enables efficient multi-hop reasoning.

Formally, given textual chunks 𝒞={c i}\mathcal{C}=\{c_{i}\}, the LLM extracts entities and typed relations to construct the knowledge graph:

𝒢=Φ LLM​(𝒞)=(𝒱,ℛ),\mathcal{G}\;=\;\Phi_{\mathrm{LLM}}(\mathcal{C})\;=\;(\mathcal{V},\mathcal{R}),(4)

where each node v∈𝒱 v\in\mathcal{V} and each relation r∈ℛ r\in\mathcal{R} maintain persistent references to their supporting text chunks:

ℓ v:𝒱→𝒫​(𝒞),ℓ v​(v)={Chunks supporting​v},\ell_{v}:\mathcal{V}\to\mathcal{P}(\mathcal{C}),\quad\ell_{v}(v)=\{\text{Chunks supporting }v\},(5)

ℓ r:ℛ→𝒫​(𝒞),ℓ r​(r)={Chunks supporting​r}.\ell_{r}:\mathcal{R}\to\mathcal{P}(\mathcal{C}),\quad\ell_{r}(r)=\{\text{Chunks supporting }r\}.(6)

Activating an entity or relation in 𝒢\mathcal{G} simultaneously triggers the corresponding original dialogue text 𝒞\mathcal{C} and any associated multimodal data ℳ\mathcal{M}, ensuring that both symbolic and perceptual knowledge remain accessible and grounded. This design compresses raw memory into a compact, graph-structured form while preserving traceability and enabling rich relational reasoning.

### 3.2 Parametric Memory

Parametric memory is stored as weights in a lightweight language model, and the memory in the LTM can be updated through periodic supervised fine-tuning. This parameterized model aims to alleviate the significant computational and storage overhead introduced during RAG inference by mimicking the behavior of nonparametric retrievers and enabling plug-and-play domain memory adaptation through context.

#### Memory Encoding.

The parametric memory module is realized as a small-scale language model ℳ LLM\mathcal{M}_{\text{LLM}}, which stores internalized knowledge within its parameters. Unlike the explicit knowledge graph that preserves symbolic relations, the parametric memory encodes knowledge in a distributed manner through gradient-based parameter updates. The model learns to simulate the retrieval process by directly generating the retrieved answer from a given question. For each training instance consisting of a question q q and its corresponding retrieved context ℛ\mathcal{R}, the model produces an answer ℛ^\hat{\mathcal{R}} as:

ℛ^=ℳ LLM​(q,ℛ),\hat{\mathcal{R}}=\mathcal{M}_{\text{LLM}}(q,\mathcal{R}),(7)

where ℛ\mathcal{R} is obtained from the explicit knowledge graph via a retrieval-augmented generation (RAG) module. In practice, we construct supervision pairs (q,ℛ)(q,\mathcal{R}) such that the model learns to internalize retrieved knowledge as part of its parametric representation.

#### Memory Initialization.

The parametric memory is initialized with the original pretrained parameters of the base language model, denoted as:

ℳ parametric 0=Θ pretrained,\mathcal{M}_{\text{parametric}}^{0}=\Theta_{\text{pretrained}},(8)

where Θ pretrained\Theta_{\text{pretrained}} represents the original model weights .The base model f base f_{\text{base}} can be instantiated from any pretrained multimodal or language model (e.g., LLM or VLM), and our implementation optionally adopts a 7B-scale transformer for efficiency. At this stage, the model has no task-specific memory. The initialization provides a neutral state from which subsequent fine-tuning gradually embeds retrieved knowledge into the parameters.

#### Supervised Fine-tuning.

To enable the model to internalize retrieved knowledge, we perform supervised fine-tuning on the constructed dataset of (q,ℛ,ℛ^)(q,\mathcal{R},\hat{\mathcal{R}}) triplets. Each training example consists of a question q q, multiple-choice context, and a retrieved answer ℛ\mathcal{R} from the explicit memory. The model is optimized to generate ℛ\mathcal{R} given q q, effectively learning the retrieval process itself. The training objective minimizes the token-level cross-entropy loss between the generated sequence ℛ^\hat{\mathcal{R}} and the target ℛ\mathcal{R}:

To enable the model to internalize retrieved knowledge from the LTM, we perform supervised fine-tuning on a constructed dataset of (q,ℛ,ℛ^)(q,\mathcal{R},\hat{\mathcal{R}}) triplets derived from the LTM retrieval process. Each training instance consists of a question q q, its multiple-choice context, and the corresponding retrieved answer ℛ\mathcal{R} provided by the explicit memory module. The model is optimized to generate ℛ\mathcal{R} conditioned on q q, thereby learning to emulate the retrieval behavior of the LTM within its parametric space.

Formally, the training objective minimizes the token-level cross-entropy loss between the generated sequence ℛ^\hat{\mathcal{R}} and the target ℛ\mathcal{R}:

ℒ update=−∑t=1 T log⁡P Θ​(r t∣q,r<t),\mathcal{L}_{\text{update}}=-\sum_{t=1}^{T}\log P_{\Theta}(r_{t}\mid q,r_{<t}),(9)

where r t r_{t} denotes the t t-th token of the target sequence ℛ\mathcal{R}, r<t r_{<t} represents its preceding tokens, and Θ\Theta are the current model parameters. This process embeds retrieved knowledge directly into the parameter space, serving as the parametric memory update mechanism.

#### Dynamic Memory Expansion.

Parametric memory is dynamically updated as the explicit knowledge graph expands. At each update step t t, newly retrieved pairs (q t,ℛ t)(q_{t},\mathcal{R}_{t}) are used to fine-tune the model, producing an updated parameter state:

ℳ parametric t+1=ℳ parametric t+Δ​Θ t,\mathcal{M}_{\text{parametric}}^{t+1}=\mathcal{M}_{\text{parametric}}^{t}+\Delta\Theta_{t},(10)

where Δ​Θ t\Delta\Theta_{t} represents the gradient-based weight update induced by fine-tuning on newly accumulated data. This continual process enables the model to progressively encode new knowledge, ensuring that parametric memory evolves in synchrony with the explicit memory without requiring full retraining.

#### Training Implementation.

The fine-tuning process is implemented using a compact autoregressive language model architecture with parameter-efficient optimization. To ensure scalability, training is conducted in mixed precision (bfloat16) with gradient checkpointing to reduce memory consumption. Automatic mixed-precision training and gradient scaling are employed to improve computational efficiency. The optimization is performed using AdamW with a cosine learning rate scheduler for stable convergence. Each training batch consists of input prompts of the form:

Prompt:“Question:​q​Choices:​c 1,c 2,…,c n​”,\text{Prompt: }\text{``Question: }q\text{ Choices: }c_{1},c_{2},\dots,c_{n}\text{''},(11)

paired with the corresponding retrieved content ℛ\mathcal{R} from the explicit memory. The model learns to generate ℛ\mathcal{R} conditioned on the prompt, effectively transforming the non-parametric retrieval process into a parameterized parametric memory representation through supervised fine-tuning.

4 Experiment
------------

### 4.1 Setups

#### Evaluation Datasets.

We evaluate the proposed method on three multimodal reasoning benchmarks with distinct characteristics:

*   •ScienceQA[Lu_2022_ScienceQA]. This dataset contains 21,208 multimodal science questions combining textual and visual contexts, with 48.7% of instances containing images. Questions span physics, chemistry, and biology domains, requiring cross-modal reasoning between textual concepts and visual diagrams. Additionally, ScienceQA offers image captions to aid text-only LLMs in reasoning, allowing a comparison of unimodal approaches. 
*   •LoCoMo[Maharana_2024_locomo]. This dataset contains 10 high-quality, very long-term multi-modal dialogues, with each conversation encompassing approximately 600 turns and 16,618 tokens on average, distributed over up to 32 sessions. Dialogues are generated by LLM-based agents grounded in unique personas and temporal event graphs, and are equipped with image-sharing and reaction capabilities. 
*   •MSR-VTT[Xu_2016_MSR-VTT]. A large-scale video–text benchmark comprising 10,000 YouTube video clips across 20 diverse categories, each paired with 20 human-annotated captions, totaling 200K video–sentence pairs. MSR-VTT is widely adopted for video–text retrieval and cross-modal understanding, assessing a model’s ability to align dynamic visual content with natural-language semantics. 

#### Baselines.

We compare the proposed method against a broad range of state-of-the-art LLMs and VLMs to assess both unimodal and multimodal reasoning capabilities. For ScienceQA, we compare models across general-domain reasoning in zero- and few-shot settings, covering both text-only and multimodal paradigms. Specifically, text-based LLMs include GPT Model[Lu_2022_ScienceQA], CoT[Lu_2022_ScienceQA], HoT-T5-Large [Yao_2023_Thinking] and DDCoT[Zheng_2023_Advances], while multimodal VLMs encompass, LaVIN[Luo_2023_LaVIN], BLIP-2, CCOT[Mitra_2024_CCOT], and GraphVis[Deng_2024_GraphVis]. We also include the tool-augmented VLM Chameleon[Lu_2023_Chameleon] and the RAG-enhanced model VaLiK[Liu_2025_VaLiK] as strong multimodal reasoning baselines. For LoCoMo, we include GPT-3.5-Turbo[Brown_2020_GPT3] and Qwen2.5-7B-Instruct[Yang_2025_Qwen3], covering strong proprietary and open-source models with robust reasoning and grounding abilities. All models are tested under identical prompting and evaluation protocols to ensure fair cross-modal comparison. For MSR-VTT, We compare our approach with a comprehensive set of representative methods, including InternVideo [wang2022internvideo], UMT-L [li2023unmasked], CLIP-VIP [xue2022clip], mPLUG-2 [xu2023mplug], VAST [chen2024vast], CLIP2TV [gao2021clip2tv], DRL [wang2022disentangled], TS2-Net [liu2022ts2], CLIP4Clip [luo2022clip4clip], X-CLIP [ma2022x], DMAE [jiang2023dual], Cap4Video++ [cap4video++], TeachClip [holistic], ExCae [yang2025expertized] and the CLIP [radford2021clip] baseline. Our baseline uses only the CLIP text encoder, which yields an extremely lightweight model compared with video based architectures.

#### Implementation.

For multimodal processing, we handle each modality separately. For images, we use GPT-4o-mini to convert them into captions. For audio, we employ Whisper [Raford_2023_Whisper] to obtain textual representations. For videos, we sample frames and process them with a VLM to generate captions. We construct three types of memory: core, semantic, and episodic. Entities and relations are then extracted from these memories to build a MMKG, where each entity node and relation edge is linked to multiple text blocks corresponding to the original multimodal data [Liu_2025_VaLiK].To ensure fairness, in the LoCoMo dataset we exclude the fifth category from comparisons.GPT-4o-mini is used as the LLM for both knowledge graph construction and retrieval.

We construct MMKGs using the training set of ScienceQA and the LoCoMo dialogue dataset, which serves as the generation basis for parameterized memory. For model training, we adopt Qwen2.5-7B as the backbone model and employ supervised fine-tuning to encode the knowledge from ScienceQA and LoCoMo datasets into the model’s weight parameters. The fine-tuning follows the causal language modeling paradigm, with the input-output format designed as ”Question-Retrieved” and loss computed exclusively on the output segment. Key training configurations are set as follows: sequence length of 2048, AdamW optimizer with a learning rate of 2×10−6 2\times 10^{-6}, linear learning rate scheduler with 10% warm-up steps, and gradient clipping with a maximum norm of 1.0. All experiments are conducted using at most a single A100 80G GPU.

### 4.2 Main Results

Method#T-Param Subject Context Modality Grade Average
NAT SOC LAN TXT IMG NO G1-6 G7-12
Human[Lu_2022_ScienceQA]-90.23 84.97 87.48 89.60 87.50 88.10 91.59 82.42 88.40
GPT-4[Liu_2023_LLaVa]-84.06 73.45 87.36 81.87 70.75 90.73 84.69 79.10 82.69
CoT (GPT-3)[Lu_2022_ScienceQA]173B 75.44 70.87 78.09 74.68 67.43 79.93 78.23 69.68 75.17
CoT (UnifiedQA)[Lu_2022_ScienceQA]223M 71.00 76.04 78.91 66.42 66.53 81.81 77.06 68.82 74.11
CoT (GPT-4)[Lu_2023_Chameleon]1T+85.48 72.44 90.27 82.65 71.49 92.89 86.66 79.04 83.99
DDCoT[Zheng_2023_Advances]175B 80.15 76.72 82.82 78.89 72.53 85.02 82.86 75.21 80.15
HoT-T5-Large [Yao_2023_Thinking]738M 84.46 79.08 84.64 82.89 75.81 88.15 83.88 82.47 83.38
Chameleon (ChatGPT)[Lu_2023_Chameleon]175B+81.62 70.64 84.00 79.77 70.80 86.62 81.86 76.53 79.93
LaVIN-13B[Yang_2023_Mm]13B--------77.54
BLIP-2[Yang_2023_Mm]---------74.17
CCOT[Mitra_2024_CCOT]7B--------76.84
GraphVis[Deng_2024_GraphVis]7B--------73.18
Qwen2.5-7B[Liu_2025_VaLiK]7B 76.20 67.83 77.27 74.49 65.79 79.02 77.72 69.35 74.72
Qwen2.5-7B (Mmkg)[Liu_2025_VaLiK]7B 73.98 66.37 78.18 71.65 64.30 79.65 76.51 68.03 73.47
Qwen2.5-7B (Visual Genome)[Liu_2025_VaLiK]7B 76.78 67.04 78.09 74.05 66.19 79.72 78.08 69.68 75.08
Qwen2.5-7B (MemVerse)7B 74.51 68.5 78.73 75.92 66.19 81.95 79.70 64.73 75.62
Qwen2.5-72B[Liu_2025_VaLiK]72B 79.64 67.10 84.90 77.56 65.00 87.93 80.25 74.85 78.37
Qwen2.5-72B (MemVerse)72B 77.53 68.95 85.36 78.68 66.39 89.20 82.31 77.76 80.25
GPT-4o-mini-77.31 73.45 86.91 74.05 66.86 87.93 83.37 71.85 76.82
GPT-4o-mini (MemVerse)-85.26 81.55 89.09 83.28 78.19 91.50 88.11 80.75 85.48

Table 1: Performance comparison (%) on ScienceQA benchmark. #T-Params denotes trainable parameters. Categories: NAT (natural science), SOC (social science), LAN (language), TXT (text context), IMG-Cap (image caption), NO (no context), G1-6 (grades 1-6), G7-12 (grades 7-12). Method groups: (1) Human performance baseline, (2) Zero/Few-shot text-only LLMs, (3) Zero/Few-shot Multimodal VLMs, (4) LLMs/VLMs enhanced with memory mechanisms or external knowledge for multimodal reasoning. The best results are marked in bold, the second-best results are underlined. 

Table[1](https://arxiv.org/html/2512.03627v1#S4.T1 "Table 1 ‣ 4.2 Main Results ‣ 4 Experiment ‣ MemVerse: Multimodal Memory for Lifelong Learning Agents") presents the performance of various methods on the ScienceQA benchmark. Overall, our MemVerse enhanced models achieve the state-of-the-art performance across most evaluation metrics. Specifically, the highest accuracy in terms of average score is 85.48%, achieved by the GPT-4o-mini equipped with MemVerse. For subject-specific scores, the MemVerse-enhanced model also obtains the top performance in natural science with 85.26%, social science with 81.55%, and language with 89.09%. In terms of context modalities, it achieves the best results for both text context with 83.28% and image caption with 78.19%, demonstrating that our memory mechanism effectively leverages multimodal knowledge.

It is worth noting that, on the ScienceQA dataset, short-term memory contributes relatively little, as the test questions are largely non-sequential with limited contextual dependencies. The parametric memory, trained using the long-term memory, achieves comparable accuracy to long-term retrieval but with significantly faster access. Specifically, using the RAG approach requires on average 20.17 seconds per question, while retrieving from the compressed long-term memory only takes 8.26 seconds on average. The parametric memory further reduces the average retrieval time to 2.28 seconds, achieving an acceleration of approximately 89% compared to RAG and 72% compared to long-term retrieval, while maintaining similar performance. This demonstrates that parametric memory offers a practical trade-off between speed and effectiveness in knowledge-augmented reasoning.

Table 2: Comparison with SOTA methods on MSR-VTT dataset (underline marks the best ViT-based results)

Method Backbone text-to-video video-to-text
R@1↑\uparrow R@5↑\uparrow R@10↑\uparrow R@1↑\uparrow R@5↑\uparrow R@10↑\uparrow
Pre-trained foundation model
InternVideo [wang2022internvideo]ViT-H/14 55.2 79.6 87.5 57.9 79.2 86.4
UMT-L [li2023unmasked]ViT-L/14 58.8 81.0 87.1 58.6 81.6 86.5
CLIP-VIP [xue2022clip]ViT-B/16 57.7 80.5 88.2–––
mPLUG-2 [xu2023mplug]ViT-L/14 53.1 77.6 84.7–––
VAST [chen2024vast]ViT-G/14 63.9 84.3 89.6–––
ViT-based
CLIP2TV [gao2021clip2tv]ViT-B/16 49.3 74.7 83.6 46.9 75.0 85.1
DRL [wang2022disentangled]ViT-B/16 49.4 76.4 84.2 47.0 77.1 84.4
TS2-Net [liu2022ts2]ViT-B/16 47.8 76.8 85.2 47.8 76.0 84.6
Clip4Clip [luo2022clip4clip]ViT-B/16 46.4 72.1 82.0 45.4 73.4 82.4
X-CLIP [ma2022x]ViT-B/16 49.3 75.8 84.8 48.9 76.8 84.5
DMAE [jiang2023dual]ViT-B/16 49.9 75.8 85.5 49.6 76.3 85.0
Cap4Video++ [cap4video++]ViT-B/16 52.3 76.8 85.8 50.0 75.9 86.0
TeachClip [holistic]ViT-B/16 48.0 75.9 83.5–––
ExCae [yang2025expertized]ViT-G/14 67.7 92.7 96.2 69.3 92.5 96.3
CLIP [radford2021clip]-29.7 48.9 58.8 21.4 38.6 44.3
MemVerse-90.4 95.6 98.1 89.2 92.7 99.0

Furthermore, we observe that the impact of memory enhancement differs between Qwen and GPT-4o-mini. For Qwen, adding memory results in only modest improvements, whereas GPT-4o-mini benefits significantly from MemVerse. Our analysis suggests that GPT-based models are more capable of leveraging retrieved knowledge, carefully integrating it into reasoning steps. In contrast, Qwen struggles to connect retrieved content with the question context, which can result in errors even when correct information is retrieved. These findings indicate that effective memory utilization in downstream tasks requires not only high-quality retrieval but also careful prompt design to guide the model in learning and applying relevant knowledge, rather than simply injecting information indiscriminately.

To further evaluate the generalization ability of our method, we conduct additional experiments on the MSR-VTT dataset. At test time, we first use the query text to retrieve relevant memory entries from the knowledge graph and then rewrite the query by concatenating it with the retrieved information before performing the final matching. As shown in Table[2](https://arxiv.org/html/2512.03627v1#S4.T2 "Table 2 ‣ 4.2 Main Results ‣ 4 Experiment ‣ MemVerse: Multimodal Memory for Lifelong Learning Agents"), our MemVerse approach achieves an R@1 score of 90.4% for text-to-video retrieval and 89.2% for video-to-text retrieval, compared with 29.7% and 21.4% for the CLIP baseline, respectively. This corresponds to an improvement of 60.7% and 67.8% percentage points, demonstrating that incorporating a memory-based knowledge graph and rewriting queries with retrieved information dramatically enhances semantic matching on MSR-VTT.

Notably, our approach achieves these improvements without exposing the ground-truth alignment between captions and videos to the knowledge graph or the paramatric module, ensuring fair evaluation. During memory construction, pairs of captions (the original caption and the caption generated from the video) are partially aligned and connected through GPT-4o-mini’s powerful understanding, judgment, and reasoning capabilities, forming linked representations that are stored in the memory. During retrieval, the query text can effectively leverage these stored associations, resulting in highly accurate matching. As a result, this memory-based approach overcomes the limitations of directly using large LLMs or VLMs for retrieval over massive multimodal candidate pools. Training such large video models is extremely costly due to the scale of the data, and their limited context windows prevent them from processing all candidates simultaneously. By storing semantic relationships extracted with the understanding, judgment, and reasoning capabilities of models like GPT-4o-mini, our method allows lightweight embedding-based models to efficiently retrieve highly relevant entries while still leveraging the rich reasoning captured in the memory. This effectively combines the scalability of embedding-based retrieval with the semantic power of large pretrained models, demonstrating the key advantage of our approach.

### 4.3 Further Analysis

Due to space constraints, the full quantitative and qualitative results on the Locomo dataset are provided in Appendix C. We investigate the periodic update strategy of the parametric memory on the ScienceQA test set. Using a grouped partitioning scheme, we simulate incremental knowledge accumulation and examine how different update intervals influence the stability and long-term retention of distilled memory. The detailed setup, ablations, and temporal performance curves are presented in Appendix D. Furthermore, to assess the scalability of MemVerse, we conduct comprehensive experiments across primary model sizes ranging from Qwen2.5 1.5B to 72B, while keeping the parametric memory module fixed as a Qwen2.5-7B model. This design allows us to isolate the impact of scaling the main model and analyze how memory efficiency and reasoning accuracy evolve with increasing capacity, with all additional results and discussions presented in Appendix E. These studies provide deeper insights into the scalability and adaptability of MemVerse across different tasks and configurations.

5 Conclusion
------------

We introduced MemVerse, a model-agnostic, plug-and-play memory framework that brings AI agents closer to lifelong multimodal intelligence. MemVerse unifies fast parametric recall with slow, hierarchical retrieval-based memory, mirroring the complementary roles of intuition and deliberation. This dual-path design lets agents maintain recent context, transform raw multimodal experiences into structured knowledge, and retrieve information efficiently without unbounded memory growth. Across diverse multimodal tasks, MemVerse yields significant improvements in reasoning accuracy, stability, and long-horizon coherence. These results demonstrate that scalable memory, rather than ever-larger models, is the missing ingredient for continuous-learning agents. Looking ahead, we plan to explore more adaptive memory-control strategies and to deploy MemVerse in open-world environments across a variety of domains.
