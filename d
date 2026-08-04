[33mcommit 7198ceeb0c3960ac6571213b4ed509f3a383e197[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Mon Aug 3 17:50:07 2026 +0300

    hy3

[33mcommit 62a9f9e9a7de4998c141c7dedccba2d3ff6f34a4[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Fri Jul 31 01:20:58 2026 +0300

    refactor(memory): introduce MemoryActor enum and separate GovernancePolicy from MemoryLifecycle (C2.1.5)

[33mcommit ecc41c7596166061e8d1483dc0c9e3764ac37768[m
Author: qwen.ai[bot] <qwenlm-intl@service.alibaba.com>
Date:   Thu Jul 30 19:17:00 2026 +0000

    feat(memory): Phase C0 - Event Sourcing Foundation with Atomicity
    
    - Implement MemoryEvent and EventType enums for audit trail
    - Add EventStore with atomic writes inside MemoryStore transactions
    - Implement reconstruct_fact_state for true event sourcing replay
    - Fix supersede semantics: only explicit replacement triggers SUPERSEDED event
    - Distinguish Reflection vs Pattern events
    - Add legacy provenance markers for migration
    - Add comprehensive tests for atomicity, replay, and event semantics

[33mcommit 8706ebf515745f92f42d2d0a068c60f2ad4bebfb[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Mon Jul 27 00:59:56 2026 +0300

    feat(memory): implement Phase 3 episod

[33mcommit 0daf32b4182f3ca494627c48dfd756f7ea47d4bc[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Fri Jul 17 22:12:07 2026 +0300

    Massive refactoring: async isolation, SQLite concurrency, FAISS memory footprint optimization, Tarjan SCC graph algorithm, and multimodal LLM pipeline fixes

[33mcommit 95daabff8b86603152f059b05b2398634fd51006[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Mon Jul 6 22:51:47 2026 +0300

    delmref

[33mcommit 7b8d72ae14f5659cfb36c85ce17d49845106649a[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Mon Jul 6 22:06:49 2026 +0300

    delref

[33mcommit b173f75c2c3c829b0e73769ec46c8af5d27091c3[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Mon Jul 6 17:12:09 2026 +0300

    start w claude

[33mcommit c1e5e75d61d5a17a035cc249e9ab10a209e5c21a[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Fri Jul 3 13:56:42 2026 +0300

    audit

[33mcommit bd3c3c89c1b043de5385c059ce178612a32251e2[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Wed Jul 1 00:34:55 2026 +0300

    feat: переключение на gemini-3.1-flash-lite, тесты пройдены

[33mcommit 42092eb126c4e774494f39d709a5c81dbd9491b4[m[33m ([m[1;31morigin/ai-fixes[m[33m)[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Tue Jun 30 17:11:17 2026 +0300

    Save state before applying AI fixes

[33mcommit e42e14312a1338a05b6e415e9e81202680b664e1[m
Author: Amargon1337 <dorodnikov.ivan@gmail.com>
Date:   Wed Jun 24 23:33:15 2026 +0300

    Первый коммит: перенос проекта и LLM-анализатор
