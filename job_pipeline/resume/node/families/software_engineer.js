// Generic software-engineer resume. Section order comes from
// spec.section_order (driven by job_pipeline/resume/families.py); this
// wrapper just hands the spec to the shared assembler in base.js.
const { assembleResume } = require("../base");

module.exports.render = (spec, outputPath) => assembleResume(spec, outputPath);
