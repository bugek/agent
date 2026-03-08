# AI Code Agent Product Roadmap

## Product Vision

AI Code Agent จะพัฒนาจาก autonomous coding scaffold ไปเป็น product สำหรับรับ requirement, วิเคราะห์ codebase, แก้ไขโค้ด, ทดสอบ, review, และเปิด PR ได้อย่างน่าเชื่อถือในหลาย stack โดยเริ่มจาก Python และขยายไปสู่ Next.js / NestJS / full-stack application workflow

## Product Goals

1. ทำให้ agent รัน end-to-end ได้จริงในเครื่อง developer และ CI
2. เพิ่มความแม่นในการค้นหาและแก้ไขโค้ดระดับหลายไฟล์
3. รองรับ modern application stacks โดยเฉพาะ Next.js และ NestJS
4. ยกระดับจาก code assistant เป็น product workflow ที่มี observability, safety, reviewability และ deployment readiness

## Product Principles

1. Hybrid retrieval ก่อน LLM brute force
2. Safe-by-default editing และ explicit validation ทุกครั้ง
3. Framework-aware behavior แทน prompt เดียวใช้ได้ทุกงาน
4. Product quality สำคัญพอๆ กับ code generation quality
5. ให้ fallback mode ใช้งานได้แม้ provider ภายนอกหรือ sandbox บางส่วนไม่พร้อม

## Roadmap Phases

### Phase 0: Foundation

เป้าหมาย: ทำให้แกนระบบใช้งานได้จริงในระดับ MVP

ผลลัพธ์หลัก:
1. CLI workflow รันได้
2. LLM provider abstraction ใช้งานได้
3. Sandbox และ smoke tests ทำงานได้
4. Git-backed local workflow พร้อมเอกสารพื้นฐาน

### Phase 1: Reliable Code Agent Core

เป้าหมาย: เพิ่มความน่าเชื่อถือของการวิเคราะห์ แก้ไข และตรวจสอบ

ผลลัพธ์หลัก:
1. Multi-file edit operations
2. Better planner context gathering
3. Deterministic validation flow
4. Health checks และ provider diagnostics
5. Structured execution logs

### Phase 2: Framework-Aware Product

เป้าหมาย: รองรับ application stacks ที่ใช้จริงในตลาด

ผลลัพธ์หลัก:
1. Next.js project detection และ workflow
2. NestJS project detection และ workflow
3. JavaScript/TypeScript build, lint, test support
4. Monorepo awareness ระดับเริ่มต้น

### Phase 3: Retrieval and Accuracy Layer

เป้าหมาย: เพิ่มความแม่นในการหา context และ cross-file reasoning

ผลลัพธ์หลัก:
1. Hybrid code index
2. Symbol graph และ import graph
3. Semantic retrieval
4. Repo memory และ pattern reuse

### Phase 4: Frontend Product Quality

เป้าหมาย: สร้าง application front ที่มีคุณภาพด้าน UX/UI สูงขึ้น ไม่ใช่แค่ scaffold

ผลลัพธ์หลัก:
1. Design brief input
2. Frontend-specific prompt policies
3. Reusable UI architecture
4. Visual review workflow
5. Responsive and state coverage checks

Current progress:
1. Started on the deterministic Next.js path.
2. Generated frontend scaffolds now include clearer visual direction and non-trivial surface styling.
3. Generated components now include loading, empty, error, and success states by default.
4. App Router scaffolds can emit `loading.tsx` and `error.tsx` alongside route pages.

### Phase 5: Team and Production Readiness

เป้าหมาย: ใช้งานระดับทีมและระบบจริงได้ปลอดภัยขึ้น

ผลลัพธ์หลัก:
1. CI integration
2. Hosted or remote sandbox options
3. Policy controls และ approval gates
4. Metrics, audit trails, and failure analysis
5. PR automation และ issue integration ที่ครบขึ้น

## 12-Month Direction

### Quarter 1

1. ทำ core runtime ให้เสถียร
2. เพิ่ม multi-file editing
3. รองรับ JS/TS validation pipeline

### Quarter 2

1. รองรับ Next.js และ NestJS
2. เพิ่ม project-type detection
3. เพิ่ม framework-specific prompts และ testers

### Quarter 3

1. เพิ่ม hybrid retrieval layer
2. เพิ่ม semantic index และ symbol graph
3. ปรับ planner/coder ให้ใช้ context แบบมีอันดับความสำคัญ

### Quarter 4

1. เพิ่ม frontend design quality workflow
2. เพิ่ม CI/CD integration และ team controls
3. เตรียม packaging ให้เป็น product มากขึ้น

## Success Metrics

1. Task completion rate ของ change requests
2. First-pass test pass rate
3. Review rejection rate
4. Average time from issue to validated patch
5. Retrieval precision for target files
6. Framework-specific success rate for Python, Next.js, NestJS

## Current Recommendation

ลำดับที่ควรทำต่อจากจุดนี้:

1. Multi-file edit engine
2. JS/TS tester pipeline
3. Next.js support
4. NestJS support
5. Hybrid code index