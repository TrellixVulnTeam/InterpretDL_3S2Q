import numpy as np
from tqdm import tqdm
from collections.abc import Iterable

from .abc_interpreter import InputGradientInterpreter, IntermediateGradientInterpreter
from ..data_processor.readers import images_transform_pipeline, preprocess_save_path
from ..data_processor.visualizer import explanation_to_vis, show_vis_explanation, save_image

class SmoothGradInterpreter(InputGradientInterpreter):
    """
    Smooth Gradients Interpreter.

    For input gradient based interpreters, the target issue is generally the vanilla input gradient's noises.
    The basic idea of reducing the noises is to use different similar inputs to get the input gradients and 
    do the average. 

    Smooth Gradients method solves the problem of meaningless local variations in partial derivatives
    by adding random noise to the inputs multiple times and take the average of the gradients.

    More details regarding the Smooth Gradients method can be found in the original paper:
    http://arxiv.org/abs/1706.03825.
    """

    def __init__(self, paddle_model: callable, device: str = 'gpu:0', use_cuda=None):
        """

        Args:
            paddle_model (callable): A model with :py:func:`forward` and possibly :py:func:`backward` functions.
            device (str): The device used for running ``paddle_model``, options: ``"cpu"``, ``"gpu:0"``, ``"gpu:1"`` 
                etc.
        """

        InputGradientInterpreter.__init__(self, paddle_model, device, use_cuda)

    def interpret(self,
                  inputs: str or list(str) or np.ndarray,
                  labels: list or np.ndarray = None,
                  noise_amount: int = 0.1,
                  n_samples: int = 50,
                  resize_to: int = 224,
                  crop_to: int = None,
                  visual: bool = True,
                  save_path: str = None) -> np.ndarray:
        """The technical details of the SmoothGrad method are described as follows:
        SmoothGrad generates ``n_samples`` noised inputs, with the noise scale of ``noise_amount``, and then computes 
        the gradients *w.r.t.* these noised inputs. The final explanation is averaged gradients.

        Args:
            inputs (str or list): The input image filepath or a list of filepaths or numpy array of read images.
            labels (list or np.ndarray, optional): The target labels to analyze. The number of labels should be equal 
                to the number of images. If None, the most likely label for each image will be used. Default: None.
            noise_amount (int, optional): Noise level of added noise to the image. The std of Gaussian random noise 
                is ``noise_amount`` * (x :sub:`max` - x :sub:`min`). Default: ``0.1``.
            n_samples (int, optional): The number of new images generated by adding noise. Default: ``50``.
            resize_to (int, optional): Images will be rescaled with the shorter edge being ``resize_to``. Defaults to 
                ``224``.
            crop_to (int, optional): After resize, images will be center cropped to a square image with the size 
                ``crop_to``. If None, no crop will be performed. Defaults to ``None``.
            visual (bool, optional): Whether or not to visualize the processed image. Default: ``True``.
            save_path (str, optional): The filepath(s) to save the processed image(s). If None, the image will not be 
                saved. Default: ``None``.

        Returns:
            np.ndarray: the explanation result.
        """

        imgs, data = images_transform_pipeline(inputs, resize_to, crop_to)
        # print(imgs.shape, data.shape, imgs.dtype, data.dtype)  # (1, 224, 224, 3) (1, 3, 224, 224) uint8 float32

        bsz = len(data)

        self._build_predict_fn(gradient_of='probability')

        # obtain the labels (and initialization).
        _, predicted_label, predicted_proba = self.predict_fn(data, labels)
        self.predicted_label = predicted_label
        self.predicted_proba = predicted_proba
        if labels is None:
            labels = predicted_label
        labels = np.array(labels).reshape((bsz, ))

        # SmoothGrad
        max_axis = tuple(np.arange(1, data.ndim))
        stds = noise_amount * (np.max(data, axis=max_axis) - np.min(data, axis=max_axis))

        total_gradients = np.zeros_like(data)
        for i in tqdm(range(n_samples), leave=True, position=0):
            noise = np.concatenate(
                [np.float32(np.random.normal(0.0, stds[j], (1, ) + tuple(d.shape))) for j, d in enumerate(data)])
            _noised_data = data + noise
            gradients, _, _ = self.predict_fn(_noised_data, labels)
            total_gradients += gradients

        avg_gradients = total_gradients / n_samples

        # visualize and save image.
        if save_path is None and not visual:
            # no need to visualize or save explanation results.
            pass
        else:
            save_path = preprocess_save_path(save_path, bsz)
            for i in range(bsz):
                # print(imgs[i].shape, avg_gradients[i].shape)
                vis_explanation = explanation_to_vis(imgs[i],
                                                     np.abs(avg_gradients[i]).sum(0),
                                                     style='overlay_grayscale')
                if visual:
                    show_vis_explanation(vis_explanation)
                if save_path[i] is not None:
                    save_image(save_path[i], vis_explanation)

        # intermediate results, for possible further usages.
        self.labels = labels

        return avg_gradients


class SmoothGradNLPInterpreter(IntermediateGradientInterpreter):
    """
    Integrated Gradients Interpreter for NLP tasks.
        
    For input gradient based interpreters, the target issue is generally the vanilla input gradient's noises.
    The basic idea of reducing the noises is to use different similar inputs to get the input gradients and 
    do the average. 

    The inputs for NLP tasks are considered as the embedding features. So the noises or the changes of inputs
    are done for the embeddings.

    More details regarding the Integrated Gradients method can be found in the original paper:
    https://arxiv.org/abs/1703.01365.
    """

    def __init__(self, paddle_model: callable, device: str = 'gpu:0', use_cuda: bool = None) -> None:
        """
        
        Args:
            paddle_model (callable): A model with :py:func:`forward` and possibly :py:func:`backward` functions.
            device (str): The device used for running ``paddle_model``, options: ``"cpu"``, ``"gpu:0"``, ``"gpu:1"`` 
                etc.
        """
        IntermediateGradientInterpreter.__init__(self, paddle_model, device)

    def interpret(self,
                  raw_text: str,
                  tokenizer: callable = None,
                  text_to_input_fn: callable = None,
                  label: list or np.ndarray = None,
                  noise_amount: int = 0.1,
                  n_samples: int = 50,
                  embedding_name: str = 'word_embeddings',
                  max_seq_len: int = 128,
                  visual: bool = False) -> np.ndarray:
        """The technical details of the IntGrad method for NLP tasks are similar for CV tasks, except the noises are
        added on the embeddings.

        Args:
            data (tupleornp.ndarray): The inputs to the NLP model.
            labels (listornp.ndarray, optional): The target labels to analyze. If None, the most likely label 
                will be used. Default: ``None``.
            steps (int, optional): number of steps in the Riemann approximation of the integral. Default: ``50``.
            embedding_name (str, optional): name of the embedding layer at which the noises will be applied. 
                The name of embedding can be verified through ``print(model)``. Defaults to ``word_embeddings``. 

        Returns:
            np.ndarray or tuple: explanations, or (explanations, pred).
        """
        assert (tokenizer is None) + (text_to_input_fn is None) == 1, "only one of them should be given."

        # tokenizer to text_to_input_fn.
        if tokenizer is not None:
            def text_to_input_fn(raw_text):
                encoded_inputs = tokenizer(text=raw_text, max_seq_len=max_seq_len)
                # order is important. *_batched_and_to_tuple will be the input for the model.
                _batched_and_to_tuple = tuple([np.array([v]) for v in encoded_inputs.values()])
                return _batched_and_to_tuple
        else:
            print("Warning: Visualization can not be supported if tokenizer is not given.")

        # from raw text string to token ids (and other terms that the user-defined function outputs).
        model_input = text_to_input_fn(raw_text)
        if isinstance(model_input, Iterable) and not hasattr(model_input, 'shape'):
            model_input = tuple(inp for inp in model_input)
        else:
            model_input = tuple(model_input, )

        self._build_predict_fn(layer_name=embedding_name, gradient_of='probability')

        gradients, label, _, proba = self.predict_fn(model_input, label, noise_amount=None)

        # SG
        total_gradients = np.zeros_like(gradients)
        for i in tqdm(range(n_samples), leave=True, position=0):
            gradients, _, _, _ = self.predict_fn(model_input, label, noise_amount=noise_amount)
            total_gradients += gradients

        sg_gradients = total_gradients / n_samples

        # intermediate results, for possible further usages.
        self.predicted_label = label
        self.predicted_proba = proba

        if visual:
            # TODO: visualize if tokenizer is given.
            print("Visualization is not supported yet.")
            print("Currently please see the tutorial for the visualization:")
            print("https://github.com/PaddlePaddle/InterpretDL/blob/master/tutorials/ernie-2.0-en-sst-2.ipynb")

        return sg_gradients
