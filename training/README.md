# Fine-tuning on your answers

Teaches your model the answers the game has accepted, using Google Colab's free GPU (your
laptop has no NVIDIA GPU, which local training needs). The notebook hasn't been run yet,
so treat the first run as a trial.

## 1. Export your feedback

In the app, open **Answer history…** and click **Export training data…**, or run:

```bash
python krillion_solver.py export-training
```

That writes two files (by default to `training/data`):

- `training_examples.jsonl`: one example per prompt that has accepted answers, in the
  exact format the app sends, so the trained model keeps working in the app.
- `rejected_answers.jsonl`: answers the game rejected, for reference.

More feedback gives better results. A few dozen prompts with accepted answers is a
reasonable minimum.

## 2. Train in Colab

1. Open [KrillionSolver_Colab.ipynb](KrillionSolver_Colab.ipynb) in Google Colab
   (File → Upload notebook).
2. Choose **Runtime → Change runtime type → T4 GPU**.
3. In the settings cell, pick the base model that matches the one you use: `llama3.2:3b`
   or `llama3.1:8b`.
4. Run the cells in order and upload `training_examples.jsonl` when asked.
5. The last cell downloads `krillion-solver-model.zip`. Download it before closing Colab,
   because Colab deletes its files when the session ends.

## 3. Add it to Ollama

Extract the zip, open PowerShell in the extracted folder, and run:

```bash
ollama create krillion-solver -f Modelfile
```

Then press **↻** next to Model in the app and choose `krillion-solver`.

Fine-tuning changes what the model knows only once you train again. Feedback you give
afterwards affects the app's rankings right away, but not the model.
