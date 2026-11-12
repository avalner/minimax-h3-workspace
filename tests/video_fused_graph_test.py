#!/usr/bin/env python3
"""The Video section's second model: the Fused Turbo graph, and that FastH3 stays as it was.

    python tests/video_fused_graph_test.py

Pure Python: no ComfyUI, no aiohttp. It builds exactly the graphs the server sends and checks the
recipe of MATLOWAI's fused file — 4 steps, shift 12 / 3, SLA sparse attention, no LoRA file — and
that the fused checkpoint is offered as an optional extra, never as part of an install plan.
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

import catalog  # noqa: E402
import workflows as wf  # noqa: E402

MODELS = {"fast_unet": "MinimaxH3\\fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors",
          "lipsync_unet": "MinimaxH3\\Minimax-h3_Singularity_ref2va_v1.3_int8.safetensors",
          "ref2v_turbo_lora": "MinimaxH3\\minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
          "text_encoder": "MinimaxH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
          "video_vae": "MinimaxH3\\minimax_h3_video_vae_int8_convrot.safetensors",
          "audio_vae": "MinimaxH3\\minimax_h3_audio_vae_fp32.safetensors"}
FUSED = "MinimaxH3\\minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"
SLA = "MinimaxH3\\minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))


def node(g, cls):
    return next((n for n in g.values() if n["class_type"] == cls), None)


def main():
    # the catalog entry: one file, downloadable, tagged for hand-picking
    entry = catalog.model_entry("fused_unet", "int8")
    check("the fused file is one int8 checkpoint", entry["variant"] == "int8" and entry["category"] == "diffusion_models")
    check("its download link is the model card's file",
          entry["url"] == "https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/main/diffusion_models/"
                           "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors", entry["url"])
    check("it is saved under MinimaxH3 like the other H3 models", entry["name"].startswith("MinimaxH3\\"), entry["name"])
    check("its name passes the role check", catalog.looks_right("fused_unet", entry["file"]))
    check("a wrong file is still warned about", not catalog.looks_right("fused_unet", "qwen_image_vae.safetensors"))
    check("Fused Turbo is picked by default when the file is there", catalog.DEFAULT_CHOICE.get("fused_unet") == "int8")
    check("it never lands in an install plan", "fused_unet" not in catalog.roles_for(["video"])
          and "fused_unet" in catalog.EXTRA_ROLES)
    check("the size is what Hugging Face reports (21 GB)", abs(entry["size"] - 20_980_000_000) < 2e6, str(entry["size"]))

    # the fused graph (Image-to-Video with first and last frame)
    g, stages = wf.video_graph(MODELS, "a cat", 1152, 640, 5, 42, "first.png", "last.png", (), None, FUSED)
    loader = node(g, "UNETLoader")
    check("the fused file is the UNET", loader and loader["inputs"]["unet_name"] == FUSED, str(loader))
    check("weight dtype stays default (INT8 is in the file)", loader and loader["inputs"]["weight_dtype"] == "default")
    shift = node(g, "MiniMaxH3SigmaShift")
    check("shift is 12 / 3, the checkpoint's own recipe", shift and (shift["inputs"]["shift_video"], shift["inputs"]["shift_audio"]) == (12, 3), str(shift))
    sparse = node(g, "BlockSparseAttention")
    check("sparse attention uses the SLA pattern it was distilled for", sparse and sparse["inputs"]["selection"] == "sla", str(sparse))
    check("SLA keeps 10% of the key blocks (sparsity 0.90)", sparse and sparse["inputs"]["selection.keep_percent"] == 10.0)
    check("no LoRA node is added: both LoRAs are inside the file", node(g, "LoraLoaderModelOnly") is None)
    sched = node(g, "BasicScheduler")
    check("4 steps, simple, denoise 1", sched and (sched["inputs"]["steps"], sched["inputs"]["scheduler"], sched["inputs"]["denoise"]) == (4, "simple", 1.0), str(sched))
    check("res_multistep sampler (better sound than euler at 4 steps)",
          node(g, "KSamplerSelect")["inputs"]["sampler_name"] == "res_multistep")
    check("no CFG: BasicGuider", node(g, "BasicGuider") is not None)
    cond = node(g, "MiniMaxH3ImageToVideo")
    check("both frames are wired into the conditioning", cond and cond["inputs"].get("first_frame") and cond["inputs"].get("last_frame"),
          str(cond and sorted(cond["inputs"])))
    check("progress stages still cover loading, sampling and saving",
          stages.get("13") == "sample" and stages.get("14") == "decode" and stages.get("17") == "save", str(stages))

    # the old paths must not move
    g0, _ = wf.video_graph(MODELS, "a cat", 1152, 640, 5, 42)
    check("by default the FastH3 file is loaded", node(g0, "UNETLoader")["inputs"]["unet_name"] == MODELS["fast_unet"])
    check("the default is still 8 steps, shift 10 / 3, VSA sparse attention",
          (node(g0, "BasicScheduler")["inputs"]["steps"], node(g0, "MiniMaxH3SigmaShift")["inputs"]["shift_video"],
           node(g0, "BlockSparseAttention")["inputs"]["selection"]) == (8, 10, "vsa"))
    check("the default adds no LoRA either", node(g0, "LoraLoaderModelOnly") is None)

    g1, _ = wf.video_graph(MODELS, "a cat", 1152, 640, 5, 42, None, None, (), SLA)
    check("the fast draft still stacks the Turbo-SLA LoRA", node(g1, "LoraLoaderModelOnly") is not None)
    check("the fast draft is 4 steps, shift 6 / 3", (node(g1, "BasicScheduler")["inputs"]["steps"],
                                                     node(g1, "MiniMaxH3SigmaShift")["inputs"]["shift_video"]) == (4, 6))
    check("the fast draft still drops sparse attention", node(g1, "BlockSparseAttention") is None)

    g2, _ = wf.video_graph(MODELS, "a cat", 1152, 640, 5, 42, None, None, (("my.safetensors", 0.8),), None, FUSED)
    chain = node(g2, "LoraLoaderModelOnly")
    check("the user's own LoRA still applies on top of the fused file", chain and chain["inputs"]["lora_name"] == "my.safetensors")
    check("the fused recipe keeps shift 12 / 3 with a custom LoRA", node(g2, "MiniMaxH3SigmaShift")["inputs"]["shift_video"] == 12)

    # References mode with the same file (the card's 05_ref2va_4step_sla graph)
    gr, rstages = wf.ref_graph(MODELS, "p", 1152, 640, 5, 42, ["ref.png"], [], [], 8, "match", (), None, FUSED)
    check("references: the fused file is the UNET", node(gr, "UNETLoader")["inputs"]["unet_name"] == FUSED)
    check("references: no turbo LoRA is stacked", node(gr, "LoraLoaderModelOnly") is None)
    rshift = node(gr, "MiniMaxH3SigmaShift")
    check("references: shift 12 / 3", rshift and (rshift["inputs"]["shift_video"], rshift["inputs"]["shift_audio"]) == (12, 3), str(rshift))
    check("references: SLA sparse attention", node(gr, "BlockSparseAttention")["inputs"]["selection"] == "sla")
    check("references: 4 steps, simple, res_multistep", (node(gr, "BasicScheduler")["inputs"]["steps"],
                                                          node(gr, "BasicScheduler")["inputs"]["scheduler"],
                                                          node(gr, "KSamplerSelect")["inputs"]["sampler_name"]) == (4, "simple", "res_multistep"))
    condr = node(gr, "MiniMaxH3ReferenceToVideo")
    check("references: the picture goes into the reference conditioning", condr and condr["inputs"].get("ref_images.ref_image_0"), str(condr and sorted(condr["inputs"])))
    check("references: the step choice is ignored (4 steps is the file's recipe)", node(gr, "BasicScheduler")["inputs"]["steps"] == 4)
    check("references: progress stages cover sampling and saving", rstages.get("14") == "sample" and rstages.get("18") == "save")

    gr0, _ = wf.ref_graph(MODELS, "p", 1152, 640, 5, 42, ["ref.png"], [], [], 6, "max", (), None, None)
    check("references: the Singularity path still loads its own model", node(gr0, "UNETLoader")["inputs"]["unet_name"] == MODELS["lipsync_unet"])
    l0 = [n["inputs"] for n in gr0.values() if n["class_type"] == "LoraLoaderModelOnly"]
    check("references: the Singularity path still stacks its 4-step LoRA at 0.7", l0 and l0[0]["lora_name"].endswith("ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors") and l0[0]["strength_model"] == 0.7)
    check("references: the Singularity path still uses beta / euler, shift 12 / 4",
          (node(gr0, "BasicScheduler")["inputs"]["scheduler"], node(gr0, "KSamplerSelect")["inputs"]["sampler_name"],
           node(gr0, "MiniMaxH3SigmaShift")["inputs"]["shift_audio"]) == ("beta", "euler", 4))
    check("references: the Singularity path has no sparse attention node", node(gr0, "BlockSparseAttention") is None)

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
