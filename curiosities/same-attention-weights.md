# A `10` versus two `1`s. Which side wins?

Three fixed weights are shared by both sides:

```text
weights = [0.7, 0.2, 0.1]
A       = [0,   0,  10]
B       = [1,   1,   0]
```

Before calculating: does A's single `10` beat B's two `1`s?

## Reveal

Every number only counts through the weight above it:

```text
A = 0.7×0 + 0.2×0 + 0.1×10 = 1.0
B = 0.7×1 + 0.2×1 + 0.1×0  = 0.9
```

A wins by `0.1`. The large number nearly loses because it sits in the lightest-weight slot.

Now change just one number. What is the smallest edit you can make to B that flips the winner? Or move the `10` to a different slot and predict the new gap before calculating.

→ [Try the public weighted-sum curiosity and reveal every term](https://huggingface.co/spaces/noqt/yumi-same-weights-gradio)

The public project accepts three small JSON arrays, checks them, and returns both term-by-term products and dot products. Use public, non-secret synthetic numbers only and follow any access or quota requirement the provider presents.

This is transparent scalar weighted-sum arithmetic, not a model run, a full attention mechanism, private Yumi behavior, or evidence about real-model interpretability, security, or performance. Availability can change.

