"""The ComfyUI backend: the graph it builds, the queue and the history it reads, the cancel it asks for.

A fake ComfyUI answers from a table, so the adapter is checked without a server and without a render.

    python tests/unit/test_comfy_backend.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import workflows as wf
from backends.base import BackendError, BackendUnavailable, SubmitRejected
from backends.comfy import ComfyBackend
from domain.jobs import InputRef, JobRecord, JobSpec, JobState

MODELS = {"fast_unet": "fast.safetensors", "text_encoder": "te.safetensors",
          "video_vae": "video-vae.safetensors", "audio_vae": "audio-vae.safetensors"}
LORAS = (("lora.safetensors", 0.8),)


class FakeComfy:
    """ComfyUI as a table of answers; every call is remembered."""

    def __init__(self, queue=None, history=None, prompt=None):
        self.calls = []
        self.queue = queue or {"queue_running": [], "queue_pending": []}
        self.history = history or {}
        self.prompt = prompt if prompt is not None else (200, {"prompt_id": "p-1", "number": 3})

    async def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/prompt":
            return self.prompt
        if path == "/queue":
            return 200, self.queue
        if path.startswith("/history/"):
            return 200, self.history
        if path == "/interrupt":
            return 200, {}
        raise AssertionError("unexpected request: " + path)


def spec(**overrides):
    kwargs = dict(prompt="кадр", aspect="16:9", quality="720p", seconds=5, seed=42, width=1280, height=720,
                  models=MODELS)
    kwargs.update(overrides)
    return JobSpec.video(**kwargs)


def record(state=JobState.QUEUED, backend_job_id="p-1"):
    return JobRecord(job_id="j1", prompt_id=backend_job_id, backend_job_id=backend_job_id, state=state)


class SubmitTest(unittest.IsolatedAsyncioTestCase):
    def backend(self, comfy):
        return ComfyBackend(client=comfy, url="http://127.0.0.1:8188", client_id="c1",
                            models=lambda: MODELS, loras=lambda mode: list(LORAS))

    async def test_the_graph_comes_from_the_spec_and_is_posted(self):
        comfy = FakeComfy()
        result = await self.backend(comfy).submit(spec())
        self.assertEqual(result.backend_job_id, "p-1")
        self.assertIn("save", result.stages.values(), "этапы нужны окну, чтобы показывать прогресс")
        method, path, kwargs = comfy.calls[0]
        self.assertEqual((method, path), ("POST", "/prompt"))
        self.assertEqual(kwargs["json"]["prompt"], result.graph)
        self.assertEqual(kwargs["json"]["client_id"], "c1")
        expected, _ = wf.video_graph(MODELS, "кадр", 1280, 720, 5, 42, None, None, LORAS, None, None)
        self.assertEqual(result.graph, expected, "рецепт графа изменился")

    async def test_the_pictures_of_the_job_reach_the_graph(self):
        comfy = FakeComfy()
        inputs = (InputRef(role="Первый кадр", kind="image", comfy="first.png", key="first_frame"),
                  InputRef(role="Последний кадр", kind="image", comfy="last.png", key="last_frame"))
        result = await self.backend(comfy).submit(spec(inputs=inputs))
        images = {node["inputs"].get("image") for node in result.graph.values() if node["class_type"] == "LoadImage"}
        self.assertEqual(images, {"first.png", "last.png"})

    async def test_a_model_the_engine_does_not_have_is_refused_before_the_post(self):
        comfy = FakeComfy()
        with self.assertRaises(SubmitRejected):
            await self.backend(comfy).submit(spec(model="fused"))
        self.assertEqual(comfy.calls, [], "движок не должен получить задание без нужной модели")

    async def test_the_engines_refusal_keeps_its_reason(self):
        comfy = FakeComfy(prompt=(400, {"error": {"message": "Prompt outputs failed validation"},
                                        "node_errors": {"5": {"class_type": "UNETLoader",
                                                              "errors": [{"message": "model not found"}]}}}))
        with self.assertRaises(SubmitRejected) as caught:
            await self.backend(comfy).submit(spec())
        self.assertIn("validation", str(caught.exception))
        self.assertIn("UNETLoader", str(caught.exception))

    async def test_a_silent_engine_is_not_a_refusal(self):
        async def down(method, path, **kwargs):
            raise BackendUnavailable("нет связи")

        with self.assertRaises(BackendUnavailable):
            await self.backend(down).submit(spec())


class InspectTest(unittest.IsolatedAsyncioTestCase):
    def backend(self, comfy):
        return ComfyBackend(client=comfy, url="http://127.0.0.1:8188", client_id="c1", models=lambda: MODELS)

    async def test_the_state_of_a_job(self):
        cases = (
            ({"queue_running": [["9", "p-1", {}, {}, {}]], "queue_pending": []}, {}, "running"),
            ({"queue_running": [], "queue_pending": [["9", "p-1", {}, {}, {}]]}, {}, "queued"),
            ({"queue_running": [], "queue_pending": []}, {"p-1": {"outputs": {"1": {}}}}, "finished"),
            ({"queue_running": [], "queue_pending": []},
             {"p-1": {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "OOM"}]]}}},
             "failed"),
            ({"queue_running": [], "queue_pending": []}, {}, "unknown"),
        )
        for queue, history, expected in cases:
            result = await self.backend(FakeComfy(queue=queue, history=history)).inspect(record())
            self.assertEqual(result.state, expected, (queue, history))

    async def test_a_job_without_an_engine_id_is_unknown(self):
        result = await self.backend(FakeComfy()).inspect(record(backend_job_id=None))
        self.assertEqual(result.state, "unknown")

    async def test_the_outputs_of_a_finished_job(self):
        comfy = FakeComfy(history={"p-1": {"outputs": {"9": {"images": [{"filename": "clip.mp4"}]}}}})
        self.assertEqual((await self.backend(comfy).outputs(record())),
                         {"9": {"images": [{"filename": "clip.mp4"}]}})


class CancelTest(unittest.IsolatedAsyncioTestCase):
    def backend(self, comfy):
        return ComfyBackend(client=comfy, url="http://127.0.0.1:8188", client_id="c1", models=lambda: MODELS)

    async def test_a_queued_job_is_removed_from_the_queue(self):
        comfy = FakeComfy()
        await self.backend(comfy).cancel(record(JobState.QUEUED))
        self.assertEqual(comfy.calls[0][:2], ("POST", "/queue"))
        self.assertEqual(comfy.calls[0][2]["json"], {"delete": ["p-1"]})

    async def test_a_running_job_is_interrupted_by_its_own_id(self):
        comfy = FakeComfy()
        await self.backend(comfy).cancel(record(JobState.RUNNING))
        self.assertEqual(comfy.calls[0][:2], ("POST", "/interrupt"))
        self.assertEqual(comfy.calls[0][2]["json"], {"prompt_id": "p-1"},
                         "отмена чужого задания не должна останавливать текущее")

    async def test_a_job_without_an_engine_id_cannot_be_cancelled(self):
        with self.assertRaises(BackendError):
            await self.backend(FakeComfy()).cancel(record(backend_job_id=None))


if __name__ == "__main__":
    unittest.main()
