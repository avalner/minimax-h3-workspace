"""What a fresh install needs: ComfyUI build, node packs, and model variants per GPU class.

Every model has a role (what the workflows use it for). A role can have several
variants; `choose_models` picks one per role from the detected hardware.
"""

COMFY_VERSION = "0.45.0"   # 0.43.0 has neither RelayVSR (fast video upscale) nor MiniMaxH3ImageConditioning (H3 Image)
MIN_DRIVER = 580  # the bundled PyTorch is built for CUDA 13
COMFY_URL = ("https://github.com/Comfy-Org/ComfyUI/releases/download/"
             f"v{COMFY_VERSION}/ComfyUI_windows_portable_nvidia.7z")
COMFY_SIZE = 2_071_479_423   # the v0.45.0 NVIDIA archive; 0.43.0 was 2_040_000_000
COMFY_DIRNAME = "ComfyUI_windows_portable"

# Downloaded as GitHub archives of a fixed commit, so users need no git.
NODE_PACKS = [
    {"name": "ComfyUI-H3-NativeAudioLock", "repo": "SveSop/ComfyUI-H3-NativeAudioLock",
     "commit": "699e58e51b1c8e12b8c0f0dd55da603d870d20e6"},
    {"name": "Comfyui_Minimax_h3_latent_Upscaler", "repo": "LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler",
     "commit": "40316cf008b2fd8663263270669eb4da23f89d2c"},
    # separates the voice from music in the lipsync audio editor
    {"name": "ComfyUI-MelBandRoFormer", "repo": "kijai/ComfyUI-MelBandRoFormer",
     "commit": "92c86854e6654f4aacc97484471af95c98ea16d4"},
    # loads reference videos at 24 fps for the references mode
    {"name": "ComfyUI-VideoHelperSuite", "repo": "Kosinkadink/ComfyUI-VideoHelperSuite",
     "commit": "4d907bee61e92c2e65af3bd6383a4e4d356126d1"},
    # memory savers (MiniMaxChunkFeedForward, MiniMaxLowVRAMAttention) used by the long video graphs
    {"name": "ComfyUI-KJNodes", "repo": "kijai/ComfyUI-KJNodes",
     "commit": "ab72eda7ffad6280422d71ab7d54d3fb87c0ba18"},
    # The exact action workflow (PM «Minimax H3 Best Action Workflow»): SelfLift samples 8 steps at 0.6x, lifts the
    # latent with the learned upscaler and finishes with 4 steps at full size; YCNodes adds the finishing sigma step.
    # Both were read before they were added: no network, no shell, no exec, no extra pip packages; pinned commits.
    {"name": "comfyui-SelfLift", "repo": "facok/comfyui-SelfLift",
     "commit": "835c3cf7919410f71f414688f049c6ca497789d0"},          # no LICENSE file in the repository
    {"name": "ComfyUI-YCNodes-MiniMax-H3", "repo": "yichengup/ComfyUI-YCNodes-MiniMax-H3",   # MIT, the version the workflow was made with
     "commit": "efbe83f69ffa97a251a575ce42edc7bf8005da0c"},
]

# What each mode needs from custom nodes: checked in a user's own ComfyUI (a new install gets them by itself).
# `classes` are node names ComfyUI must know; `optional` ones only switch a feature on.
NODE_CHECKS = [
    {"pack": "ComfyUI-H3-NativeAudioLock", "classes": ["MiniMaxH3NativeAudioLock"], "modes": ["lipsync"],
     "optional": False, "why": "точный липсинг под ваше аудио"},
    {"pack": "Comfyui_Minimax_h3_latent_Upscaler", "classes": ["MinimaxH3LatentUpscaler3D"], "modes": ["lipsync", "action"],
     "optional": False, "why": "второй проход (увеличение кадра)"},
    {"pack": "ComfyUI-MelBandRoFormer", "classes": ["MelBandRoFormerSampler", "MelBandRoFormerModelLoader"],
     "modes": ["lipsync"], "optional": True, "why": "кнопка «Отделить голос» в редакторе аудио"},
    {"pack": "ComfyUI-VideoHelperSuite", "classes": ["VHS_LoadVideo"], "modes": ["refs", "swap"],
     "optional": False, "why": "видео, в котором меняем персонажа"},
    {"pack": "ComfyUI-KJNodes", "classes": ["MiniMaxChunkFeedForward", "MiniMaxLowVRAMAttention"], "modes": ["long"],
     "optional": False, "why": "экономия видеопамяти в длинном видео"},
    {"pack": "comfyui-SelfLift", "classes": ["SelfLiftH3Sampler"], "modes": ["action"],
     "optional": False, "why": "точный режим экшн-сцен (8 шагов в низком разрешении + 4 в высоком)"},
    {"pack": "ComfyUI-YCNodes-MiniMax-H3", "classes": ["H3SigmaRefiner"], "modes": ["action"],
     "optional": False, "why": "точный режим экшн-сцен (доводочный шаг)"},
]


def node_repo(pack):
    return next(p["repo"] for p in NODE_PACKS if p["name"] == pack)


HF = "https://huggingface.co/{repo}/resolve/main/{path}"
SUB = "MinimaxH3"  # subfolder inside each model category, same as the original workflows
IMG_SUB = "Images"


def _m(repo, path, size_gb, category, name=None, sub=SUB):
    """A downloadable file. `name` is how it is saved (defaults to the file name in the repo)."""
    fname = name or path.rsplit("/", 1)[-1]
    return {"url": HF.format(repo=repo, path=path), "size": int(size_gb * 1e9), "category": category,
            "file": fname, "name": f"{sub}\\{fname}" if sub else fname}


# the same file is published under two names; either one counts as present
FILE_ALIASES = {
    "minimax_h3_latent_upscaler_3d_bf16.safetensors": ("minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors",),
}

VARIANTS = {
    "fast_unet": {
        "int8": _m("FastVideo/FastVideo-FastH3-Comfy",
                   "diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors", 22.13, "diffusion_models"),
        "nvfp4": _m("FastVideo/FastVideo-FastH3-8-Step-V2-NVFP4-Comfy",
                    "diffusion_models/fastvideo_fasth3_8step_v2_pruned_nvfp4.safetensors", 13.61, "diffusion_models"),
    },
    # Optional second main model for Image-to-Video: the MATLOWAI fused turbo bake. The FL2VA transformer with the
    # reference delta merged in, lightx2v's turbo LoRA and Mystic v2 already folded into the weights, quantised once
    # afterwards: 4 steps, no LoRA files, and it also does references in the same file. Licence: MiniMax H3 Community.
    "fused_unet": {
        "int8": _m("MATLOWAI/minimax-h3-fused-turbo-int8-convrot",
                   "diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors",
                   20.98, "diffusion_models"),
    },
    "lipsync_unet": {
        "full": _m("WarmBloodAban/Minimax-h3_Singularity",
                   "Minimax-h3_Singularity_ref2va_v1.3_int8.safetensors", 34.0, "diffusion_models"),
        "pruned": _m("WarmBloodAban/Minimax-h3_Singularity",
                     "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors", 20.97, "diffusion_models"),
        "lite": _m("WarmBloodAban/Minimax-h3_Singularity",
                   "Minimax-h3_Singularity_ref2va_v1.3_Pruned_w4a8.safetensors", 11.77, "diffusion_models"),
        # v2: viewers reported that v1.3 generates sound badly
        "v2": _m("WarmBloodAban/Minimax-h3_Singularity",
                 "Minimax-h3_Singularity_ref2va_v2_pruned_int8.safetensors", 20.97, "diffusion_models"),
        "v2_full": _m("WarmBloodAban/Minimax-h3_Singularity",
                      "Minimax-h3_Singularity_ref2va_v2_int8.safetensors", 34.0, "diffusion_models"),
    },
    "text_encoder": {
        "nvfp4": _m("Comfy-Org/MiniMax-H3", "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                    15.69, "text_encoders"),
    },
    "video_vae": {
        "int8": _m("Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_int8_convrot.safetensors", 2.81, "vae"),
    },
    "audio_vae": {
        "fp32": _m("Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors", 0.61, "vae"),
    },
    "ref2v_turbo_lora": {
        "bf16": _m("Comfy-Org/MiniMax-H3", "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
                   1.96, "loras"),
    },
    "upscaler": {
        # same file (same sha256) the original workflow knows as minimax_h3_latent_upscaler_3d_bf16
        "bf16": _m("LBH-123-AI/Minimax_h3_latent_Upscaler",
                   "minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors",
                   0.69, "latent_upscale_models", name="minimax_h3_latent_upscaler_3d_bf16.safetensors", sub=None),
    },
    # Turbo-SLA (lightx2v): 4-step LoRA for the first/last frame model; used on top of FastH3 for the "fast" switch
    "sla_lora": {
        "bf16": _m("lightx2v/Minimax-h3-Turbo-SLA", "minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors",
                   1.96, "loras"),
    },
    "preview": {
        "taeh3": _m("Alissonerdx/Minimax-H3-ComfyUI", "vae_approx/taeh3.safetensors", 0.02, "vae_approx", sub=None),
    },
    # voice / music separation (MelBand RoFormer), used by the audio editor in Lipsync
    "vocal_model": {
        "fp16": _m("Kijai/MelBandRoFormer_comfy", "MelBandRoformer_fp16.safetensors", 0.456, "diffusion_models"),
    },
    # action scenes (Prompt Mastery workflow): FL2VA/Ref2VA hybrid
    "action_unet": {
        "int8": _m("smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models",
                   "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors", 20.97, "diffusion_models"),
        "lite": _m("binglingzhimeng/minimax_h3_hybrid_fl2va_ref2va_b25-49_w6a8",
                   "minimax_h3_hybrid_fl2va_ref2va_b25-49_w6a8.safetensors", 15.98, "diffusion_models"),
    },
    "speed_slider_lora": {
        "v1.1": _m("RunningHubAI/rh-h3-speed-slider-1.1-lora", "H3_speed_slider_1.1.safetensors", 0.01, "loras"),
    },
    "action_upscaler": {
        "lms": _m("Alissonerdx/Minimax-H3-ComfyUI", "latent_upscaler/h3_upscaler_lms_v0.1.safetensors",
                  0.69, "latent_upscale_models", sub=None),
    },
    # optional LoRAs for the references mode
    "realism_lora": {
        "v1": _m("fal/MiniMax-H3-Realism-People-LoRA", "h3-realism-people-t2v-i2v-r2v.safetensors", 0.13, "loras"),
    },
    "motion_repair_lora": {
        "v1": _m("JOKER141/MiniMax-H3-General-Motion-Continuity-Repair", "Motion_Repair.safetensors", 0.16, "loras"),
    },
    # Character swap (akatz-ai): replaces a person in a video with a character from a picture
    "swap_lora": {
        "v15": _m("akatz-ai/MiniMax-H3-Character-Swap-LoRA", "h3_character_swap_v1.5.safetensors", 0.16, "loras"),
    },
    # Pika H3 Flex Turbo: 8-step HD LoRA for references; a viewer favourite for the generated sound
    "pika_lora": {
        "hd8": _m("Pika-Labs/Pika-H3-Flex-Turbo-LoRA", "pika_h3_flex_turbo_8step_hd_ref2va.safetensors", 3.91, "loras"),
    },
    # DMAD: distillation LoRA that makes the 4-step draft of the video studio (LCM sampler, shift 12 in the workflow)
    "draft_lora": {
        "bf16": _m("Kijai/MiniMax-H3-experimental", "loras/minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors",
                   0.366647, "loras"),
    },
    # improve quality of a finished video (SeedVR2, plain ComfyUI nodes)
    "seedvr_unet": {
        "int8": _m("Comfy-Org/SeedVR2", "diffusion_models/seedvr2_3b_int8_convrot.safetensors", 3.46,
                   "diffusion_models", sub=IMG_SUB),
    },
    "seedvr_vae": {
        "fp16": _m("Comfy-Org/SeedVR2", "vae/seedvr2_ema_vae_fp16.safetensors", 0.5, "vae", sub=IMG_SUB),
    },
    # fast video enlarge x2 in one sampling step (RelayVSR, built into ComfyUI); video only
    "relayvsr_unet": {
        "x2": _m("Comfy-Org/RelayVSR", "diffusion_models/relayvsr_x2_lightx2v_int8_convrot.safetensors", 5.16,
                 "diffusion_models", sub=IMG_SUB),
    },
    "relayvsr_vae": {
        "x2": _m("Comfy-Org/RelayVSR", "vae/relayvsr_flash_vae_x2_fp32.safetensors", 0.13, "vae", sub=IMG_SUB),
    },
    # pictures: Krea 2 (by text) and Qwen Image 2.1 (edit a picture, character sheet); both are plain ComfyUI nodes
    "krea_unet": {
        "int8": _m("Comfy-Org/Krea-2", "diffusion_models/krea2_turbo_int8_convrot.safetensors", 13.49,
                   "diffusion_models", sub=IMG_SUB),
    },
    "krea_clip": {
        "fp8": _m("Comfy-Org/Krea-2", "text_encoders/qwen3vl_4b_fp8_scaled.safetensors", 5.24,
                  "text_encoders", sub=IMG_SUB),
    },
    "krea_vae": {
        "bf16": _m("Comfy-Org/Krea-2", "vae/qwen_image_vae.safetensors", 0.25, "vae", sub=IMG_SUB),
    },
    # Krea 2 for realistic people and photos: RedCraft RedMix + the Realism Engine LoRA (two passes)
    "kreareal_unet": {
        "fp8": _m("Kerstal/krea-2_model", "redcraftKREA2RedMix_krea2Edition.safetensors", 13.14,
                  "diffusion_models", sub=IMG_SUB),
    },
    "kreareal_lora": {
        "v2": _m("uzumix/realism_engine_krea2_v2", "realism_engine_krea2_v2.safetensors", 1.56, "loras", sub=IMG_SUB),
    },
    "qwen_unet": {
        "int8": _m("Comfy-Org/Qwen-Image-2.1", "diffusion_models/qwen_image_2.1_int8_convrot.safetensors", 7.26,
                   "diffusion_models", sub=IMG_SUB),
    },
    "qwen_clip": {
        "int8": _m("Comfy-Org/Qwen-Image-2.1", "text_encoders/qwen3vl_8b_int8_convrot.safetensors", 9.35,
                   "text_encoders", sub=IMG_SUB),
    },
    # «Расширение» (outpaint): the LoRA is trained on a flat gray fill (#808080) and on one instruction sentence;
    # without it the model moves the picture and the stitched original does not line up
    "outpaint_lora": {
        "v2": _m("ausboss/Qwen-Image-2.1-Outpaint-LoRA", "qwen-image-2.1-outpaint-v2.safetensors", 0.159, "loras", sub=IMG_SUB),
    },
    "qwen_vae": {
        "bf16": _m("Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors", 0.68, "vae", sub=IMG_SUB),
    },
    # MiniMax H3 Image (official ComfyUI build, Comfy-Org/MiniMax-H3-Image): pictures by text and edit by references.
    # Its text encoder is the very same file as the video's «text_encoder» (qwen3vl_32b_minimax_h3_nvfp4_awq), so it is
    # never downloaded twice. The model is tuned for 4-16 MP of area: below 8 MP the picture is noticeably grainy.
    "h3img_unet": {
        "w4a8": _m("Comfy-Org/MiniMax-H3-Image", "diffusion_models/minimax_h3_image_pruned_w4a8.safetensors", 12.55,
                   "diffusion_models"),
        "int8": _m("Comfy-Org/MiniMax-H3-Image", "diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors",
                   20.98, "diffusion_models"),
    },
    "h3img_vae": {
        "fp16": _m("Comfy-Org/MiniMax-H3-Image", "vae/minimax_h3_image_vae_fp16.safetensors", 1.17, "vae"),
    },
    # FLUX.2 klein 4B (Apache 2.0, free for commercial use): edit a picture by text
    "flux_unet": {
        "fp8": _m("black-forest-labs/FLUX.2-klein-4b-fp8", "flux-2-klein-4b-fp8.safetensors", 4.07,
                  "diffusion_models", sub=IMG_SUB),
    },
    "flux_clip": {
        "bf16": _m("Comfy-Org/z_image_turbo", "split_files/text_encoders/qwen_3_4b.safetensors", 8.04,
                   "text_encoders", sub=IMG_SUB),
    },
    "flux_vae": {
        "bf16": _m("Comfy-Org/flux2-dev", "split_files/vae/flux2-vae.safetensors", 0.34, "vae", sub=IMG_SUB),
    },
}

def _known_files():
    """Every model file the app knows, by base name, with the role it belongs to.

    A user may keep a file in any subfolder of a models category: only the base name is compared,
    exactly as ComfyUI lists it in `/models/<category>`.
    """
    out = {}
    for role, variants in VARIANTS.items():
        for variant in variants.values():
            for name in (variant["file"], *FILE_ALIASES.get(variant["file"], ())):
                out.setdefault(name.lower(), role)
    return out


KNOWN_FILES = _known_files()


def file_base(name):
    """The file name without the subfolder ComfyUI may list it under (Windows or POSIX separators)."""
    return str(name or "").replace("/", "\\").split("\\")[-1]


def same_file(a, b):
    """Two names point at the same model file: the folder it is kept in does not matter."""
    a, b = file_base(a).lower(), file_base(b).lower()
    return bool(a) and a == b


# What a file name of each role must contain (any of these words, any case). Used only to warn when a file is picked
# by hand that does not look like the right one, and to say what to check when ComfyUI fails with a shape error.
ROLE_MARKS = {
    "fast_unet": ["fasth3", "h3"], "lipsync_unet": ["singularity", "h3"], "fused_unet": ["fused"], "text_encoder": ["32b"],
    "video_vae": ["video_vae"], "audio_vae": ["audio_vae"], "ref2v_turbo_lora": ["turbo"],
    "upscaler": ["upscaler"], "preview": ["taeh3"], "action_unet": ["hybrid", "fl2va"],
    "speed_slider_lora": ["speed"], "action_upscaler": ["upscaler"], "vocal_model": ["melband"],
    "realism_lora": ["realism"], "motion_repair_lora": ["motion"], "pika_lora": ["pika"], "swap_lora": ["swap"], "sla_lora": ["sla"],
    "draft_lora": ["dmad"],
    "seedvr_unet": ["seedvr"], "seedvr_vae": ["seedvr"], "relayvsr_unet": ["relayvsr"], "relayvsr_vae": ["relayvsr"], "krea_clip": ["qwen3vl", "qwen3-vl"],
    "krea_vae": ["qwen_image", "qwen-image"], "kreareal_unet": ["krea"], "kreareal_lora": ["realism"],
    "krea_unet": ["krea"],
    "qwen_unet": ["qwen"], "qwen_clip": ["qwen3vl", "qwen3-vl"], "qwen_vae": ["qwen"], "outpaint_lora": ["outpaint"],
    "flux_unet": ["flux"], "flux_clip": ["qwen_3", "qwen3"], "flux_vae": ["flux"],
    # the H3 Image checkpoint is «minimax_h3_image_…»; the VAE sits in the same repository and carries «image_vae»
    "h3img_unet": ["h3_image", "h3-image"], "h3img_vae": ["h3_image_vae", "image_vae"],
}


def looks_right(role, filename):
    """False only when the role has a name rule and the file name breaks it."""
    marks = ROLE_MARKS.get(role)
    n = filename.replace("/", "\\").split("\\")[-1].lower()
    return not marks or any(m in n for m in marks)


def foreign_marks(role, filename):
    """Roles whose distinctive mark sits in the file name while the role's own rule is broken
    (e.g. a video model file picked for «Отделение голоса»): a hard misfit, not a soft warning."""
    if looks_right(role, filename):
        return []
    n = filename.replace("/", "\\").split("\\")[-1].lower()
    own = set(ROLE_MARKS.get(role) or [])
    out = []
    for r, marks in ROLE_MARKS.items():
        if r == role:
            continue
        hit = next((m for m in marks if m not in own and m in n), None)
        if hit:
            out.append(r)
    return out


def marks_text(role):
    return " или ".join(f"«{m}»" for m in ROLE_MARKS.get(role, [])[:2])


# Plain words for the "another version" choice in the Models section (only versions of the same model replace each other)
VARIANT_LABELS = {
    "fast_unet": {"int8": "обычная (int8)", "nvfp4": "лёгкая NVFP4 — только для видеокарт RTX 50-й серии"},
    "lipsync_unet": {"full": "v1.3 полная (самая тяжёлая)", "pruned": "v1.3 облегчённая", "lite": "v1.3 лёгкая (слабее качество)",
                     "v2": "v2 облегчённая — по отзывам, звук лучше", "v2_full": "v2 полная"},
    "action_unet": {"int8": "обычная (int8)", "lite": "лёгкая (меньше памяти, чуть слабее)"},
    # the light W4A8 is the one for 12-16 GB cards; int8 is the heavier and slightly better one
    "h3img_unet": {"w4a8": "лёгкая W4A8 (12,5 ГБ)", "int8": "обычная int8 (21 ГБ)"},
}

# LoRA of the original action-scene workflow — a DIFFERENT file from the studio's 4-step draft above, and it is not a
# switch in «LoRA»: the file itself has to be in the loras folder. The same training (dareties v4, step 600, «fro») is
# published under several names: the author's own page (civitai.com/models/2837571, version dmad_larryvrhv4_merge) drops
# the «_fro» tail, on Hugging Face it comes with a «fl2v_lightx2v_v0.1_» prefix. Only the plain names: «…_fro_fulladaln»
# and «…_rev6_…» are other files.
ACTION_DMAD_LORA = "minimax_h3_dmad_full_dareties_v4_step600_comfy_fro"
ACTION_DMAD_FILES = (ACTION_DMAD_LORA + ".safetensors",
                     "minimax_h3_fl2v_lightx2v_v0.1_dareties_v4_step600_comfy_fro.safetensors",
                     "minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors")


def is_action_dmad(name):
    """Whether this file is the action DMAD LoRA: any of its published names, in any folder and any case.

    The Видеостудия's 4-step draft LoRA (minimax_h3_DMAD_4step_…) is a different training and must never be taken for
    it: the action scene would come out wrong and the error would look like a quality problem.
    """
    base = str(name).replace("/", "\\").split("\\")[-1].strip().lower()
    return base in ACTION_DMAD_FILES

ROLE_TITLES = {
    "fast_unet": "Основная модель — Image-to-Video (FastH3)",
    "fused_unet": "Основная модель — Fused Turbo (4 шага, всё в одном файле, по желанию)",
    "lipsync_unet": "Основная модель — Lipsync (Singularity)",
    "text_encoder": "Текстовый энкодер (Qwen3-VL 32B)",
    "video_vae": "Видео VAE",
    "audio_vae": "Аудио VAE",
    "ref2v_turbo_lora": "LoRA ускорения для Lipsync",
    "upscaler": "Апскейлер второго прохода",
    "preview": "Декодер живого превью",
    "action_unet": "Основная модель — Экшн-сцены (Hybrid FL2VA/Ref2VA)",
    "speed_slider_lora": "LoRA Speed Slider (экшн)",
    "action_upscaler": "Апскейлер SelfLift (экшн, точный режим)",
    "vocal_model": "Отделение голоса от музыки (MelBand RoFormer)",
    "realism_lora": "LoRA «Реализм людей» (по желанию)",
    "motion_repair_lora": "LoRA «Исправление движения» (по желанию)",
    "swap_lora": "LoRA «Замена персонажа» (akatz-ai)",
    "pika_lora": "LoRA Pika H3 Flex Turbo (ускорение для Референсов, хороший звук)",
    "draft_lora": "LoRA DMAD — быстрый черновик Видеостудии (4 шага вместо 8)",
    "sla_lora": "LoRA Turbo-SLA (быстрое ускорение для Image-to-Video)",
    "seedvr_unet": "Увеличение видео и изображений — модель SeedVR2 (3B)",
    "seedvr_vae": "Увеличение видео и изображений — VAE SeedVR2",
    "relayvsr_unet": "Быстрое увеличение видео — модель RelayVSR ×2",
    "relayvsr_vae": "Быстрое увеличение видео — VAE RelayVSR ×2",
    "krea_unet": "Основная модель — Изображение по тексту (Krea 2)",
    "krea_clip": "Текстовый энкодер Krea 2 (Qwen3-VL 4B)",
    "krea_vae": "VAE для Krea 2",
    "kreareal_unet": "Основная модель — Изображение: реализм (RedCraft, Krea 2)",
    "kreareal_lora": "LoRA Realism Engine для Krea 2 (реализм)",
    "qwen_unet": "Основная модель — Правка изображения (Qwen Image 2.1)",
    "qwen_clip": "Текстовый энкодер Qwen (Qwen3-VL 8B)",
    "qwen_vae": "VAE для Qwen Image 2.1",
    "outpaint_lora": "LoRA «Расширение изображения» (Qwen, без неё края не дорисовываются)",
    "flux_unet": "Основная модель — Правка изображения для коммерции (FLUX.2 klein 4B)",
    "flux_clip": "Текстовый энкодер FLUX.2 klein (Qwen3 4B)",
    "flux_vae": "VAE для FLUX.2",
    "h3img_unet": "Основная модель — Изображения MiniMax H3 Image (по тексту и правка по референсам)",
    "h3img_vae": "VAE для MiniMax H3 Image",
}

MODE_TITLES = {"video": "Image-to-Video", "lipsync": "Lipsync", "refs": "Референсы", "actor": "Актёрская игра", "swap": "Замена персонажа", "action": "Экшн-сцены",
               "krea": "Изображение по тексту", "kreareal": "Изображение: реализм", "qwen": "Правка изображения", "flux": "Правка изображения: FLUX.2 (можно в коммерцию)",
               "h3img": "Изображения MiniMax H3 Image (по тексту и по референсам)",
               "upscale": "Увеличение видео и изображений", "relayvsr": "Быстрое увеличение видео (RelayVSR)", "long": "Длинное видео"}

# Which roles each mode needs.
MODE_ROLES = {
    "video": ["fast_unet", "text_encoder", "video_vae", "audio_vae"],
    "lipsync": ["lipsync_unet", "text_encoder", "video_vae", "audio_vae", "ref2v_turbo_lora", "upscaler"],
    "refs": ["lipsync_unet", "text_encoder", "video_vae", "audio_vae", "ref2v_turbo_lora"],
    # same models as references: dialogue scenes with a voice sample, acting-first prompts
    "actor": ["lipsync_unet", "text_encoder", "video_vae", "audio_vae", "ref2v_turbo_lora"],
    "action": ["action_unet", "text_encoder", "video_vae", "audio_vae", "upscaler"],
    # character swap preset: same Ref2V model as References + a small swap LoRA
    "swap": ["lipsync_unet", "text_encoder", "video_vae", "audio_vae", "ref2v_turbo_lora", "swap_lora"],
    # long video / clips: scenes that continue each other (Singularity Ref2V + Pika LoRA, same files as References)
    "long": ["lipsync_unet", "text_encoder", "video_vae", "audio_vae", "pika_lora"],
    "krea": ["krea_unet", "krea_clip", "krea_vae"],
    "kreareal": ["kreareal_unet", "kreareal_lora", "krea_clip", "krea_vae"],
    "qwen": ["qwen_unet", "qwen_clip", "qwen_vae"],
    "flux": ["flux_unet", "flux_clip", "flux_vae"],
    # H3 Image: the UNET and its VAE are new, the text encoder is the video's own file (never a second copy)
    "h3img": ["h3img_unet", "text_encoder", "h3img_vae"],
    "upscale": ["seedvr_unet", "seedvr_vae"],
    "relayvsr": ["relayvsr_unet", "relayvsr_vae"],
}
# Small extras downloaded with a mode but not required to run it.
MODE_OPTIONAL = {
    "video": ["sla_lora"],
    "lipsync": ["realism_lora", "vocal_model"],
    "refs": ["pika_lora", "realism_lora", "motion_repair_lora"],
    "actor": ["realism_lora"],
    "swap": ["pika_lora", "realism_lora", "motion_repair_lora"],
    "action": ["speed_slider_lora", "action_upscaler"],
    "long": ["vocal_model", "draft_lora"],
    "qwen": ["outpaint_lora"],
}
ALL_ROLES = list(VARIANTS)

# Roles that are never part of an install plan (nobody has to download 21 GB for a mode they do not use):
# they are listed in «Модели» with their own Download button and switched on by a choice in their section.
EXTRA_ROLES = ("fused_unet",)

# The user's original files, for connecting to an existing ComfyUI that already has them.
DEFAULT_CHOICE = {"fast_unet": "int8", "fused_unet": "int8", "lipsync_unet": "full", "text_encoder": "nvfp4", "video_vae": "int8",
                  "audio_vae": "fp32", "ref2v_turbo_lora": "bf16", "upscaler": "bf16", "preview": "taeh3",
                  "action_unet": "int8", "speed_slider_lora": "v1.1", "action_upscaler": "lms", "vocal_model": "fp16",
                  "realism_lora": "v1", "motion_repair_lora": "v1", "pika_lora": "hd8", "swap_lora": "v15", "sla_lora": "bf16",
                  "draft_lora": "bf16",
                  "krea_unet": "int8", "krea_clip": "fp8", "krea_vae": "bf16", "kreareal_unet": "fp8", "kreareal_lora": "v2",
                  "qwen_unet": "int8", "qwen_clip": "int8", "qwen_vae": "bf16", "outpaint_lora": "v2",
                  "flux_unet": "fp8", "flux_clip": "bf16", "flux_vae": "bf16",
                  "h3img_unet": "int8", "h3img_vae": "fp16",
                  "seedvr_unet": "int8", "seedvr_vae": "fp16", "relayvsr_unet": "x2", "relayvsr_vae": "x2"}


PROFILES = {
    "lite": {"title": "Лёгкий", "lipsync_unet": "lite", "action_unet": "lite", "h3img_unet": "w4a8",
             "about": "Самые лёгкие версии: меньше качать и меньше памяти, качество немного ниже"},
    "standard": {"title": "Стандарт", "lipsync_unet": "v2", "action_unet": "int8",
                 "h3img_unet": "w4a8",
                 "about": "Облегчённая (pruned) Singularity v2: ~20 ГБ вместо 34, качество почти как у полной. Подходит для видеокарт 12–16 ГБ"},
    "max": {"title": "Максимум", "lipsync_unet": "full", "action_unet": "int8", "h3img_unet": "int8",
            "about": "Полные модели, как в исходных workflow"},
}


def recommended_profile(hw):
    vram, ram = hw.get("vram_gb") or 0, hw.get("ram_gb") or 0
    if vram + ram >= 48 and vram >= 24:  # "max" puts the full ~32 GB Singularity: too heavy for 16 GB cards
        return "max"
    if vram + ram >= 40:
        return "standard"
    return "lite"


def roles_for(modes, optional=True):
    roles = {r for m in modes for r in MODE_ROLES[m]} | {"preview"}
    if optional:
        roles |= {r for m in modes for r in MODE_OPTIONAL.get(m, [])}
    return [r for r in ALL_ROLES if r in roles]


def plan_size(choice, modes):
    return sum(VARIANTS[r][choice[r]]["size"] for r in roles_for(modes))


def choose_models(hw, profile=None):
    """Pick a variant per role for this computer. Returns (choice, notes, verdict, profile).

    ComfyUI keeps what does not fit in VRAM in system RAM, so the real budget is VRAM + RAM.
    """
    vram, ram = hw.get("vram_gb") or 0, hw.get("ram_gb") or 0
    blackwell = (hw.get("compute_cap") or 0) >= 12
    profile = profile if profile in PROFILES else recommended_profile(hw)
    choice = dict(DEFAULT_CHOICE, lipsync_unet=PROFILES[profile]["lipsync_unet"],
                  action_unet=PROFILES[profile]["action_unet"],
                  h3img_unet=PROFILES[profile].get("h3img_unet", "int8"))
    notes = []

    if blackwell and vram >= 16:
        choice["fast_unet"] = "nvfp4"
        notes.append("Видеокарта RTX 50-й серии: для генерации видео берём версию NVFP4 — она легче и быстрее на этих картах.")

    if hw.get("nvidia") and 0 < (hw.get("driver") or 0) < MIN_DRIVER:
        notes.insert(0, f"Драйвер видеокарты устарел ({hw['driver']:g}). Нужен {MIN_DRIVER} или новее, иначе ComfyUI не запустится — обновите драйвер.")

    cap = hw.get("compute_cap") or 0
    if not hw.get("nvidia"):
        verdict = "unsupported"
        notes.insert(0, "Приложение работает только с видеокартами NVIDIA (RTX 20-й серии и новее, от 8 ГБ памяти). "
                        "Видеокарты AMD Radeon, встроенная графика Intel и компьютеры Mac не поддерживаются.")
    elif 0 < cap < 7.5:
        verdict = "unsupported"
        notes.insert(0, "Ваша видеокарта слишком старая для этой программы. Нужна NVIDIA RTX 20-й серии или новее "
                        "(например, RTX 2060, 3060, 4060) с памятью от 8 ГБ.")
    elif vram < 8:
        verdict = "unsupported"
        notes.insert(0, f"Видеопамяти {vram:g} ГБ — для работы нужно минимум 8 ГБ (лучше 16). "
                        "На такой видеокарте генерация видео не поместится в память.")
    elif vram < 12 or ram < 32:
        verdict = "weak"
        if vram < 12:
            notes.insert(0, f"Видеопамяти {vram:g} ГБ — генерация будет медленной, лучше выбирать 480p или 720p.")
        if ram < 32:
            notes.insert(0, f"Оперативной памяти {ram:g} ГБ, а рекомендуется 32 ГБ: модели могут не поместиться, "
                            "Windows начнёт использовать файл подкачки и всё сильно замедлится.")
    else:
        verdict = "ok"
    return choice, notes, verdict, profile


def model_entry(role, variant):
    return dict(VARIANTS[role][variant], role=role, variant=variant, title=ROLE_TITLES[role])


# modes that appeared after the first release: the app offers them once after an update
OFFER_MODES = ["flux", "long", "h3img"]


# ---- own model files: does the file fit the rest of the mode (the speed-up LoRAs are made for ONE family of video models) ----
MAIN_ROLE = {"video": "fast_unet", "lipsync": "lipsync_unet", "refs": "lipsync_unet", "actor": "lipsync_unet", "swap": "lipsync_unet",
             "long": "lipsync_unet", "action": "action_unet", "krea": "krea_unet", "qwen": "qwen_unet", "flux": "flux_unet",
             "h3img": "h3img_unet", "upscale": "seedvr_unet"}
ROLE_FAMILY = {"lipsync_unet": "ref2", "fast_unet": "fast", "action_unet": "hybrid",
               "ref2v_turbo_lora": "ref2", "pika_lora": "ref2", "swap_lora": "ref2", "sla_lora": "fast", "speed_slider_lora": "hybrid"}
FAMILY_TITLE = {"ref2": "Ref2V / Singularity (референсы, Lipsync, студия)", "fast": "FastH3 (Image-to-Video)", "hybrid": "Hybrid FL2VA/Ref2VA (экшн)"}


def model_family(filename):
    """The kind of an H3 video model by its file name (None when the name does not say)."""
    n = filename.replace("/", "\\").split("\\")[-1].lower()
    if "hybrid" in n:
        return "hybrid"
    if "ref2v" in n or "singularity" in n:
        return "ref2"
    if "fasth3" in n or "fl2v" in n:
        return "fast"
    return None


def file_fit(role, filename):
    """Can this file be used for `role`? Returns (fit, why), fit is:

    - "ok"      — it is this role's file, or its type and name clearly match;
    - "unknown" — the name has the right marks but the app cannot tell the model type, so the user decides;
    - "no"      — a known file of ANOTHER role or another H3 model type: generation would break.

    The list of files offered for a hand pick uses this, so an Image-to-Video picker does not show
    LoRAs, text encoders and the models of other sections.
    """
    name = filename.replace("/", "\\").split("\\")[-1]
    other = KNOWN_FILES.get(name.lower())
    if other and other != role:
        return "no", f"это файл модели «{ROLE_TITLES.get(other, other)}»"
    want, got = ROLE_FAMILY.get(role), model_family(filename)
    if want and got and got != want:
        return "no", f"по названию это модель другого типа: {FAMILY_TITLE[got]}"
    if other == role:
        return "ok", ""
    if not looks_right(role, filename):
        foreign = foreign_marks(role, filename)
        if foreign:
            return "no", f"это файл модели «{ROLE_TITLES.get(foreign[0], foreign[0])}»"
        return "no", f"в названии нет {marks_text(role)}"
    if want and not got:
        return "unknown", "тип модели по названию не определить"
    return "ok", ""


def compat_notes(mode, role, filename, compute_cap=0.0):
    """Plain-language notes about an own model file picked for `role` in `mode`: [(level, text)], level is bad / warn / info."""
    n = filename.replace("/", "\\").split("\\")[-1].lower()
    notes = []
    if n.endswith(".gguf"):
        notes.append(("bad", "Файл в формате GGUF: наши сценарии грузят обычные .safetensors, такой файл не заработает."))
    if "nvfp4" in n and compute_cap and compute_cap < 10:
        notes.append(("warn", f"Формат nvfp4 рассчитан на видеокарты RTX 50-й серии. На вашей (compute {compute_cap}) он может не запуститься или дать ошибку."))
    want = ROLE_FAMILY.get(role)
    got = model_family(filename)
    if want and got and got != want:
        notes.append(("warn", f"По названию это модель другого типа: {FAMILY_TITLE[got]}. Этот раздел рассчитан на {FAMILY_TITLE[want]}. Скорее всего, будет ошибка или плохой результат."))
    loras = [r for r in MODE_ROLES.get(mode, []) + MODE_OPTIONAL.get(mode, []) if r.endswith("_lora") and r in ROLE_FAMILY]
    if want and role == MAIN_ROLE.get(mode) and loras:
        fam = got or want
        titles = ", ".join(f"«{ROLE_TITLES.get(r, r)}»" for r in loras)
        if got and any(ROLE_FAMILY[r] != got for r in loras):
            bad = ", ".join(f"«{ROLE_TITLES.get(r, r)}»" for r in loras if ROLE_FAMILY[r] != got)
            notes.append(("warn", f"Ускоряющие файлы этого раздела ({bad}) сделаны для другого типа моделей, чем выбранный. "
                                  "Звук, губы или картинка могут разойтись. Отключите ускорение (в разделе есть «Без Pika» или «Прежнее»)."))
        elif not got:
            notes.append(("info", f"Тип выбранной модели по названию не определить. Ускоряющие файлы раздела ({titles}) сделаны для типа {FAMILY_TITLE[fam]}: "
                                  "если ваша модель того же типа, всё будет работать. Если результат плохой, отключите ускорение."))
    if not notes:
        notes.append(("info", "Название файла подходит. Это своя модель: если генерация сломается, нажмите «Вернуть встроенную»."))
    return notes
