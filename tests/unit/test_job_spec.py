"""One request, one spec: the window and the agent must describe the same generation the same way.

    python tests/unit/test_job_spec.py
"""

from __future__ import annotations

import json
import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import workflows as wf
from domain.errors import InvalidSpec, SchemaVersionError
from domain.jobs import MAX_SEED, SCHEMA_VERSION, JobSpec, ResultPolicy

MODELS = {"fast_unet": "fast.safetensors", "text_encoder": "te.safetensors",
          "video_vae": "video-vae.safetensors", "audio_vae": "audio-vae.safetensors"}


def video(**overrides):
    kwargs = dict(prompt="девушка в лесу", aspect="16:9", quality="720p", seconds=5, seed=42,
                  width=1280, height=720)
    kwargs.update(overrides)
    return JobSpec.video(**kwargs)


class VideoSpecTest(unittest.TestCase):
    def test_the_window_and_the_agent_describe_the_same_generation(self):
        window = video(prompt=" девушка в лесу ", seconds="5", source="ui", sub="Текст → видео")
        agent = video(seconds=5.0, source="agent", sub="Текст → видео · агент")
        self.assertEqual(window.params(), agent.params(), "одна и та же генерация описана по-разному")
        only_display = {"source", "sub"}
        self.assertEqual({k: v for k, v in window.to_dict().items() if k not in only_display},
                         {k: v for k, v in agent.to_dict().items() if k not in only_display})

    def test_the_duration_is_clamped_in_one_place(self):
        self.assertEqual(video(seconds=1).seconds, 3.0)
        self.assertEqual(video(seconds=999).seconds, 25.0)
        self.assertEqual(video(seconds=900, max_seconds=15).seconds, 15.0)
        self.assertEqual(video(seconds=7.5).seconds, 7.5)

    def test_what_cannot_be_generated_is_refused_before_the_engine_sees_it(self):
        with self.assertRaises(InvalidSpec):
            video(prompt="   ")
        with self.assertRaises(InvalidSpec):
            video(seconds="много")
        with self.assertRaises(InvalidSpec):
            video(seed=-5)
        with self.assertRaises(InvalidSpec):
            video(width=0)
        with self.assertRaises(InvalidSpec):
            video(model="turbo")
        with self.assertRaises(InvalidSpec):
            video(acceleration="fast")
        with self.assertRaises(InvalidSpec):
            video(result_policy="anything")

    def test_a_missing_seed_is_chosen_once(self):
        spec = video(seed=None)
        self.assertTrue(0 <= spec.seed < MAX_SEED)
        self.assertEqual(spec.seed, spec.seed, "seed должен выбираться один раз, а не при каждом чтении")

    def test_the_model_snapshot_is_data_not_a_lookup(self):
        spec = video(models=MODELS, loras=(("lora.safetensors", 0.8),), acceleration="sla")
        data = spec.to_dict()
        self.assertEqual(data["models"], dict(sorted(MODELS.items())))
        self.assertEqual(data["loras"], [["lora.safetensors", 0.8]])
        self.assertEqual(data["acceleration"], "sla")

    def test_the_result_policy_is_explicit_and_defaults_to_the_library(self):
        self.assertEqual(video().result_policy, ResultPolicy.LIBRARY)
        self.assertEqual(video(result_policy=ResultPolicy.DRAFT).result_policy, ResultPolicy.DRAFT)
        self.assertEqual(video(result_policy="draft").result_policy, ResultPolicy.DRAFT)
        self.assertEqual(video().to_dict()["result_policy"], "library")

    def test_serialization_is_versioned(self):
        spec = video(models=MODELS)
        data = spec.to_dict()
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(JobSpec.from_dict(json.loads(json.dumps(data))), spec)

    def test_data_from_a_newer_version_is_not_reinterpreted(self):
        with self.assertRaises(SchemaVersionError):
            JobSpec.from_dict({**video().to_dict(), "schema_version": SCHEMA_VERSION + 1})

    def test_the_same_spec_builds_the_same_graph(self):
        window = video(models=MODELS, source="ui")
        agent = video(models=MODELS, source="agent")
        first, _ = wf.video_graph(window.models, window.prompt, window.width, window.height, window.seconds,
                                  window.seed, None, None, (), None)
        second, _ = wf.video_graph(agent.models, agent.prompt, agent.width, agent.height, agent.seconds,
                                   agent.seed, None, None, (), None)
        self.assertEqual(first, second, "рецепт графа не должен зависеть от того, откуда пришёл запрос")

    def test_inputs_are_part_of_the_spec(self):
        from domain.jobs import InputRef

        spec = video(inputs=(InputRef(role="Первый кадр", kind="image", comfy="h3s_first.png"),))
        data = spec.to_dict()
        self.assertEqual(data["inputs"], [{"role": "Первый кадр", "kind": "image", "comfy": "h3s_first.png"}])
        self.assertEqual(JobSpec.from_dict(data).inputs, spec.inputs)


if __name__ == "__main__":
    unittest.main()
