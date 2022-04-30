# -*- coding: utf-8 -*-
"""Ecommerce_Transformer.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1vEbhU_itU8wz19fB4u8k17qaWy_wslcr

#Uploading the libraries
"""

import os, gc
import json

from ast import literal_eval
from glob import glob
from tqdm import tqdm
from typing import Dict, Union, Optional, Any, Iterable
from datetime import datetime

import random as rd
import math as m
import numpy as np 
import pandas as pd 
import seaborn as sns

import plotly.express as px
import plotly.graph_objects as go

import matplotlib.dates as dates
import matplotlib.pyplot as plt

from sklearn.model_selection import train_test_split as split_data
from sklearn.preprocessing import LabelEncoder, MinMaxScaler, StandardScaler, RobustScaler
from sklearn.metrics import accuracy_score, precision_score, recall_score, fbeta_score
from sklearn.utils import resample

import sys

sys.path.append('../input/transformers-for-recsys')

from google.colab import drive
drive.mount('/content/drive')

"""#Load Data"""

dataset_dir = '/content/drive/MyDrive/dataset_1week.csv'

df = pd.read_csv(dataset_dir)
df.columns = ['user_session', 'user_id', 'item_ids', 'num_items', 'category_ids', 
              'session_initial_time', 'session_initial_timestamp', 'session_weekday_sin', 'session_weekday_cos', 'session_recency', 'session_actions', 
              'brand_ids', 'prices', 'relative_prices', 'day_index',]
df

def clean_list(arrays: list or str):
    if isinstance(arrays, str):
        arrays = arrays.replace("nan, ", "0, ")
        arrays = arrays.replace(", nan", ", 0")
        arrays = literal_eval(arrays)
    else:
        arrays = [0 if not i else i for i in arrays]
    return arrays

list_cols = ['category_ids', 'brand_ids', 'item_ids', 'prices', 'relative_prices',
             'session_weekday_sin', 'session_weekday_cos', 'session_recency', 'session_actions']
for col in tqdm(list_cols):
    df[col] = df[col].apply(clean_list)

df = df[df.num_items<=20]

df.num_items.hist(bins=50)

df.drop(columns=['num_items', 'day_index', 
                 'session_initial_time', 'session_initial_timestamp'], inplace=True)
df.info()

df.set_index(keys=['user_id', 'user_session'], inplace=True)
df

"""#Scheme"""

import functools
import operator


def flatten_nested_list(arr: list):
    return functools.reduce(operator.iconcat, arr, [])


class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

schema = {}

cat_feats = ["item_ids", "brand_ids", "category_ids", "session_actions"]
num_feats = ["prices", "relative_prices", 
             "session_weekday_sin", "session_weekday_cos", "session_recency"]

for feat in cat_feats+num_feats:
    print(f'Schema for {feat}')
    if feat in cat_feats:
        df[feat] = df[feat].apply(lambda x: np.array(x).astype(int))
        schema[feat] = {'type': "categorical",}
    else:
        df[feat] = df[feat].apply(lambda x: np.array(x).astype(float))
        schema[feat] = {'type': "numerical",}
    values = flatten_nested_list(df[feat].values.tolist())
    if feat == 'category_ids':
        encoder = LabelEncoder()
        encoder.fit(values)
        values = encoder.transform(values)
        df[feat] = df[feat].apply(lambda x: encoder.transform(x))
    schema[feat].update({'min_val': min(values), 
                         'max_val': max(values),
                   'embedding_dim': int(np.log(max(values))),})

print(json.dumps(schema, cls=NpEncoder, indent=4))

"""#Model"""

import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

"""##Dataset"""

class TabularSequentialDataset(Dataset):

    def __init__(self, df: pd.DataFrame, schema: dict, max_seq_len: int=20, batch_size: int=16):
        self.schema = schema
        self.dataset = self.seq_pad(df, max_seq_len)
        self.indices = list(self.dataset.index)
        self.batch_size = batch_size
        self.num_batches = int(m.ceil(len(self.dataset) / batch_size))
        self.max_seq_len = max_seq_len
        
    def seq_pad(self, df: pd.DataFrame, max_seq_len: int):
        num_cols = len(df.columns)
        for rid, rdata in tqdm(df.iterrows(), total=len(df)):
            rdata = np.stack(rdata.values, axis=-1)
            rdata_padded = np.zeros((max_seq_len, num_cols))
            rdata_padded[-len(rdata):, :] = rdata
            df.loc[rid] = rdata_padded.T.tolist()
        return df

    def __len__(self):
        return self.num_batches
    
    def shuffle(self):
        rd.shuffle(self.indices)

    def __getitem__(self, batch_id: int):
        indices = self.indices[batch_id*self.batch_size:(batch_id+1)*self.batch_size]
        data = self.dataset.loc[indices]
        tensors = dict()
        for col in data.columns:
            array = np.stack(data[col].values, axis=0)
            tensors[col] = torch.tensor(array, 
                                        dtype=torch.int if self.schema[col]['type']=='categorical' else torch.half, 
                                        device=device)
        return tensors

dataset = TabularSequentialDataset(df, schema, max_seq_len=20, batch_size=8)
batch_data = dataset[0]
batch_data

"""##Feature preprocessing"""

class FeaturePreprocessing(nn.Module):
    
    def __init__(self, schema: Dict[str, str], hidden_dim: int=64, training: bool=True):
        super(FeaturePreprocessing, self).__init__()
        self.training = training
        self.embedding = dict()
        self.hidden_dim = hidden_dim
        self.features_dim = 0
        self.features_order = list()
        for feat, stats in schema.items():
            if stats['type'] == 'categorical':
                self.embedding[feat] = nn.Embedding(num_embeddings=stats['max_val']+1, 
                                                     embedding_dim=stats['embedding_dim'])
                self.features_dim += stats['embedding_dim']
            else:
                self.features_dim += 1
            self.features_order.append(feat)
        self.normalize = nn.BatchNorm1d(num_features=self.features_dim)
        self.regularize = nn.Dropout(p=0.1369)
        self.full_connect = nn.Linear(in_features=self.features_dim, 
                                     out_features=self.hidden_dim, bias=True)
        self.activation = nn.Mish()
            
    def forward(self, tensors: Dict[str, torch.Tensor]):
        features = []
        for feat in self.features_order:
            if feat in self.embedding.keys():
                feat_tensor = self.embedding[feat](tensors[feat])
                feat_tensor = torch.swapaxes(feat_tensor, axis0=1, axis1=2)
            else:
                feat_tensor = torch.unsqueeze(tensors[feat], dim=1)
            features.append(feat_tensor)
        features = torch.cat(features, dim=1)
        features = self.normalize(features) # shape: (B, Df, L)
        features = torch.swapaxes(features, axis0=1, axis1=2)
        features = self.regularize(features) # shape: (B, L, Df)
        features = self.full_connect(features) # shape: (B, L, Dh)
        features = self.activation(features) 
        return features

feature_processor = FeaturePreprocessing(schema)
feature_processor.embedding

features = feature_processor(batch_data)
features

"""#Sequence Masking"""

from dataclasses import dataclass

@dataclass
class MaskingInfo:
    schema: torch.Tensor
    targets: torch.Tensor
        
        
class MaskSequence(nn.Module):
    """
    Base class to prepare masked items inputs/labels for language modeling tasks.
    
    Transformer architectures can be trained in different ways. Depending of the training method,
    there is a specific masking schema. The masking schema sets the items to be predicted (labels)
    and mask (hide) their positions in the sequence so that they are not used by the Transformer
    layers for prediction.
    We currently provide 4 different masking schemes out of the box:
        - Causal LM (clm)
        - Masked LM (mlm)
        - Permutation LM (plm)
        - Replacement Token Detection (rtd)
    This class can be extended to add different a masking scheme.
    
    Parameters
    ----------
    hidden_size:
        The hidden dimension of input tensors, needed to initialize trainable vector of
        masked positions.
    pad_token: int, default = 0
        Index of the padding token used for getting batch of sequences with the same length
    """
    def __init__(self, hidden_size: int,
                       padding_idx: int = 0,
            eval_on_last_item_only: bool = True, **kwargs):
        super(MaskSequence, self).__init__()
        self.padding_idx = padding_idx
        self.hidden_size = hidden_size
        self.eval_on_last_item_only = eval_on_last_item_only
        self.mask_schema: Optional[torch.Tensor] = None
        self.masked_targets: Optional[torch.Tensor] = None

        # Create a trainable embedding to replace masked interactions
        self.masked_item_embedding = nn.Parameter(torch.Tensor(self.hidden_size))
        torch.nn.init.normal_(self.masked_item_embedding, mean=0, std=.001)
    def compute_masked_targets(self, item_ids: torch.Tensor, training=False) -> MaskingInfo:
        """
        Method to prepare masked labels based on the sequence of item ids.
        It returns the true labels of masked positions and the related boolean mask.
        And the attributes of the class `mask_schema` and `masked_targets` are updated to be re-used in other modules.
        
        Parameters
        ----------
        item_ids: torch.Tensor
            The sequence of input item ids used for deriving labels of next item prediction task.
        training: bool
            Flag to indicate whether we are in `Training` mode or not.
            During training, the labels can be any items within the sequence based on the selected masking task.
            During evaluation, we are predicting the last item in the sequence.
        
        Returns
        -------
        Tuple[MaskingSchema, MaskedTargets]
        """
        assert item_ids.ndim == 2, "`item_ids` must have 2 dimensions."
        masking_info = self._compute_masked_targets(item_ids, training=training)
        self.mask_schema, self.masked_targets = masking_info.schema, masking_info.targets
        return masking_info
    def apply_mask_to_inputs(self, inputs: torch.Tensor, schema: torch.Tensor) -> torch.Tensor:
        """
        Control the masked positions in the inputs by replacing the true interaction
        by a learnable masked embedding.
        
        Parameters
        ----------
        inputs: torch.Tensor
            The 3-D tensor of interaction embeddings resulting from the ops: TabularFeatures + aggregation + projection(optional)
        schema: MaskingSchema
            The boolean mask indicating masked positions.
        """
        inputs = torch.where(schema.unsqueeze(-1).bool(),
                             self.masked_item_embedding.to(inputs.dtype),
                             inputs)
        return inputs
    def predict_all(self, item_ids: torch.Tensor) -> MaskingInfo:
        """
        Prepare labels for all next item predictions instead of last-item predictions 
                in a user's sequence.
            
        Returns
        -------
        Tuple[MaskingSchema, MaskedTargets]
        """
        # shift sequence of item-ids
        labels = item_ids[:, 1:]
        
        # As after shifting the sequence length will be subtracted by one, adding a masked item in
        # the sequence to return to the initial sequence.
        labels = torch.cat([labels,
                            torch.zeros((labels.shape[0], 1), dtype=labels.dtype).to(item_ids.device)], axis=-1)
        
        # apply mask on input where target is on padding index
        mask_labels = labels != self.padding_idx
        return MaskingInfo(mask_labels, labels)
    def forward(self, inputs: torch.Tensor, item_ids: torch.Tensor, training: bool=False) -> torch.Tensor:
        """
        Parameters
        ----------
        inputs: torch.Tensor 3D
            Interaction embeddings from: TabularFeatures + aggregation + projection(optional)
        item_ids: torch.Tensor
            Sequence of input item ids used for deriving labels of next item prediction task.
        """
        mask_info = self.compute_masked_targets(item_ids=item_ids, training=training)
        if mask_info.schema is None:
            raise ValueError("`mask_schema must be set.`")
        return self.apply_mask_to_inputs(inputs, mask_info.schema)

    def forward_output_size(self, input_size):
        return input_size

    def transformer_required_arguments(self) -> Dict[str, Any]:
        return {}

    def transformer_optional_arguments(self) -> Dict[str, Any]:
        return {}
        
    @property
    def transformer_arguments(self) -> Dict[str, Any]:
        """
        Prepare additional arguments to pass to the Transformer forward methods.
        """
        return {**self.transformer_required_arguments(), 
                **self.transformer_optional_arguments()}

class MaskedLanguageModeling(MaskSequence):
    """
    In Masked Language Modeling (mlm) you randomly select some positions of the sequence to be predicted, which are masked.
    During training, the Transformer layer is allowed to use positions on the right (future info).
    During inference, all past items are visible for the Transformer layer, which tries to predict the next item.
    
    Parameters
    ----------
    {mask_sequence_parameters}
    mlm_probability: Optional[float], default = 0.15
        Probability of an item to be selected (masked) as a label of the given sequence.
        p.s. We enforce that at least one item is masked for each sequence, so that the network can
        learn something with it.
    """

    def __init__(self, hidden_size: int,
                       padding_idx: int = 0,
                   mlm_probability: float = 0.15,
            eval_on_last_item_only: bool = True, **kwargs):
        super(MaskedLanguageModeling, self).__init__(hidden_size=hidden_size,
                                                     padding_idx=padding_idx,
                                          eval_on_last_item_only=eval_on_last_item_only,
                                                          kwargs=kwargs)
        self.mlm_probability = mlm_probability
    def _compute_masked_targets(self, item_ids: torch.Tensor, training=False) -> MaskingInfo:
        """
        Prepare sequence with mask schema for masked language modeling prediction
        the function is based on HuggingFace's transformers/data/data_collator.py
        
        Parameters
        ----------
        item_ids: torch.Tensor
            Sequence of input itemid (target) column
        
        Returns
        -------
        labels: torch.Tensor
            Sequence of masked item ids.
        mask_labels: torch.Tensor
            Masking schema for masked targets positions.
        """

        labels = torch.full(item_ids.shape, self.padding_idx, dtype=item_ids.dtype, device=item_ids.device)
        non_padded_mask = item_ids != self.padding_idx

        rows_ids = torch.arange(item_ids.size(0), dtype=torch.long, device=item_ids.device)
        
        # During training, masks labels to be predicted according to a probability, 
        #     ensuring that each session has at least 1 label to predict
        if training:
            # Selects a percentage of items to be masked (selected as labels)
            probability_matrix = torch.full(item_ids.shape, self.mlm_probability, device=item_ids.device)
            mask_labels = torch.bernoulli(probability_matrix).bool() & non_padded_mask
            labels = torch.where(mask_labels, item_ids, torch.full_like(item_ids, self.padding_idx))

            # Set at least 1 item in the sequence to mask
            random_index_by_session = torch.multinomial(non_padded_mask.float(), num_samples=1).squeeze()
            labels[rows_ids, random_index_by_session] = item_ids[rows_ids, random_index_by_session]
            mask_labels = labels != self.padding_idx

            # If a sequence has only masked labels, unmasks 1 of the labels
            sequences_with_only_labels = mask_labels.sum(dim=1) == non_padded_mask.sum(dim=1)
            sampled_labels_to_unmask = torch.multinomial(mask_labels.float(), num_samples=1).squeeze()

            labels_to_unmask = torch.masked_select(sampled_labels_to_unmask, sequences_with_only_labels)
            rows_to_unmask = torch.masked_select(rows_ids, sequences_with_only_labels)

            labels[rows_to_unmask, labels_to_unmask] = self.padding_idx
            mask_labels = labels != self.padding_idx

        else:
            if self.eval_on_last_item_only:
                last_item_sessions = non_padded_mask.sum(dim=1) - 1
                labels[rows_ids, last_item_sessions] = item_ids[rows_ids, last_item_sessions]
                mask_labels = labels != self.padding_idx
            else:
                masking_info = self.predict_all(item_ids)
                mask_labels, labels = masking_info.schema, masking_info.targets

        return MaskingInfo(mask_labels, labels)

sequence_mask = MaskedLanguageModeling(hidden_size=feature_processor.hidden_dim,
                                       padding_idx=0,
                                   mlm_probability=0.69)
features_masked = sequence_mask(inputs=feature_processor(batch_data), 
                              item_ids=batch_data['item_ids'])
features_masked.shape

def generate_square_subsequent_mask(dim):
    mask = (torch.triu(torch.ones(dim, dim))==1).transpose(0, 1)
    mask = mask.float().masked_fill(mask==0, float('-inf'))\
                       .masked_fill(mask==1, float(0.))
    return mask

generate_square_subsequent_mask(10)



"""##Sequence Processing

"""

!pip install transformers

import inspect
import transformers


class XLNetConfig(transformers.XLNetConfig):
    @classmethod
    def build(cls, d_model,
                   n_head,
                   n_layer,
            total_seq_length=None,
                   attn_type="bi",
                  hidden_act="gelu",
           initializer_range=0.01,
              layer_norm_eps=0.03,
                     dropout=0.3,
                   pad_token=0,
       log_attention_weights=False,
                     mem_len=1, **kwargs):
        return cls(d_model=d_model,
                   d_inner=d_model * 4,
                   n_layer=n_layer,
                    n_head=n_head,
                 attn_type=attn_type,
             ff_activation=hidden_act,
         initializer_range=initializer_range,
            layer_norm_eps=layer_norm_eps,
                   dropout=dropout,
              pad_token_id=pad_token,
         output_attentions=log_attention_weights,
                vocab_size=1,
                   mem_len=mem_len,
                   **kwargs)
    
    
class GPT2Prepare(nn.Module):
    
    def __init__(self, transformer, masking):
        super().__init__()
        self.transformer = transformer
        self.masking = masking

    def forward(self, inputs_embeds) -> Dict[str, Any]:
        seq_len = inputs_embeds.shape[1]
        
        # head_mask has shape n_layer x batch x n_heads x N x N
        head_mask = torch.tril(
            torch.ones((seq_len, seq_len), dtype=torch.uint8, device=inputs_embeds.device)
        ).view(1, 1, 1, seq_len, seq_len).repeat(self.transformer.config.num_hidden_layers, 1, 1, 1, 1)
        return {"inputs_embeds": inputs_embeds, 
                    "head_mask": head_mask}

    
class TransformerBlock(nn.Module):

    def __init__(self, transformer,
                       masking=None,
                prepare_module=None, 
                     output_fn=lambda model_outputs: model_outputs[0],):
        super().__init__()

        model_cls = transformers.MODEL_MAPPING[transformer.__class__]
        self.transformer = model_cls(transformer)

        if masking is not None:
            required = list(masking.transformer_required_arguments().keys())
            check = all(param in inspect.signature(self.transformer.forward).parameters for param in required)
            if not check:
                raise ValueError(f"{masking.__class__.__name__} requires the parameters: "
                                 f"{', '.join(required)} in the {type(self.transformer)} signature")

        self.masking = masking
        self.output_fn = output_fn

    def forward(self, inputs_embeds, **kwargs):
        transformer_kwargs = {"inputs_embeds": inputs_embeds}
        if self.masking:
            masking_kwargs = self.masking.transformer_arguments
            if masking_kwargs:
                transformer_kwargs.update(masking_kwargs)

        filtered_transformer_kwargs = {}
        for param in inspect.signature(self.transformer.forward).parameters:
            if param in transformer_kwargs:
                filtered_transformer_kwargs[param] = transformer_kwargs[param]
        outputs = self.transformer(**filtered_transformer_kwargs)
        outputs = self.output_fn(outputs)
        return outputs

    def _get_name(self):
        return "TransformerBlock"

    def forward_output_size(self, input_size):
        assert len(input_size) == 3
        return torch.Size([input_size[0], input_size[1], self.transformer.config.hidden_size])

backbone_config = XLNetConfig.build(d_model=feature_processor.hidden_dim, n_head=8, n_layer=3)
backbone_config

backbone = TransformerBlock(backbone_config, masking=sequence_mask)
backbone

features_attentioned = backbone(features_masked)
features_attentioned.shape

"""#Head"""

import torchmetrics as tm
from abc import abstractmethod


def check_inputs(ks, scores, labels):
    if len(ks.shape) > 1:
        raise ValueError("ks should be a 1-D tensor")

    if len(scores.shape) != 2:
        raise ValueError("scores must be a 2-D tensor")

    if len(labels.shape) != 2:
        raise ValueError("labels must be a 2-D tensor")

    if scores.shape != labels.shape:
        raise ValueError("scores and labels must be the same shape")

    return (ks.to(dtype=torch.int32, device=scores.device), scores, labels,)

def extract_topk(ks, scores, labels):
    max_k = int(max(ks))
    topk_scores, topk_indices = torch.topk(scores, max_k)
    topk_labels = torch.gather(labels, 1, topk_indices)
    return topk_scores, topk_indices, topk_labels

def create_output_placeholder(scores, ks):
    return torch.zeros(scores.shape[0], len(ks)).to(device=scores.device, dtype=torch.float32)

def tranform_label_to_onehot(labels, vocab_size):
    return one_hot_1d(labels.reshape(-1), vocab_size, dtype=torch.float32).detach()


class RankingMetric(tm.Metric):
    """
    Metric wrapper for computing ranking metrics@K for session-based task.
    
    Parameters
    ----------
    top_ks : list, default [2, 5])
        list of cutoffs
    labels_onehot : bool
        Enable transform the labels to one-hot representation
    """

    def __init__(self, top_ks=None, labels_onehot=False):
        super(RankingMetric, self).__init__()
        self.top_ks = top_ks or [2, 5]
        self.labels_onehot = labels_onehot
        # Store the mean of the batch metrics (for each cut-off at topk)
        self.add_state("metric_mean", default=[], dist_reduce_fx="cat")

    def update(self, preds: torch.Tensor, target: torch.Tensor, **kwargs):  # type: ignore
        # Computing the metrics at different cut-offs
        if self.labels_onehot:
            target = torch_utils.tranform_label_to_onehot(target, preds.size(-1))
        metric = self._metric(torch.LongTensor(self.top_ks), preds.view(-1, preds.size(-1)), target)
        self.metric_mean.append(metric)  # type: ignore

    def compute(self):
        # Computing the mean of the batch metrics (for each cut-off at topk)
        return torch.cat(self.metric_mean, axis=0).mean(0)

    @abstractmethod
    def _metric(self, ks: torch.Tensor, preds: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """
        Compute a ranking metric over a predictions and one-hot targets.
        This method should be overridden by subclasses.
        
        Parameters
        ----------
        ks : torch.Tensor or list
            list of cutoffs
        scores : torch.Tensor
            predicted item scores
        labels : torch.Tensor
            true item labels
        
        Returns
        -------
        torch.Tensor:
            list of precisions at cutoffs
        """

class PrecisionAt(RankingMetric):
    def __init__(self, top_ks=None, labels_onehot=False):
        super(PrecisionAt, self).__init__(top_ks=top_ks, labels_onehot=labels_onehot)

    def _metric(self, ks: torch.Tensor, scores: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        """ Compute precision@K for each of the provided cutoffs """
        ks, scores, labels = check_inputs(ks, scores, labels)
        _, _, topk_labels = extract_topk(ks, scores, labels)
        precisions = create_output_placeholder(scores, ks)

        for index, k in enumerate(ks):
            precisions[:, index] = torch.sum(topk_labels[:, : int(k)], dim=1) / float(k)
        return precisions


class RecallAt(RankingMetric):
    def __init__(self, top_ks=None, labels_onehot=False):
        super(RecallAt, self).__init__(top_ks=top_ks, labels_onehot=labels_onehot)

    def _metric(self, ks: torch.Tensor, scores: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        """ Compute recall@K for each of the provided cutoffs """
        ks, scores, labels = check_inputs(ks, scores, labels)
        _, _, topk_labels = extract_topk(ks, scores, labels)
        recalls = create_output_placeholder(scores, ks)

        # Compute recalls at K
        num_relevant = torch.sum(labels, dim=-1)
        rel_indices = (num_relevant != 0).nonzero()
        rel_count = num_relevant[rel_indices].squeeze()

        if rel_indices.shape[0] > 0:
            for index, k in enumerate(ks):
                rel_labels = topk_labels[rel_indices, : int(k)].squeeze()
                recalls[rel_indices, index] = torch.div(torch.sum(rel_labels, dim=-1), rel_count) \
                                                   .reshape(len(rel_indices), 1) \
                                                   .to(dtype=torch.float32)  # Ensuring type is double, because it can be float if --fp16
        return recalls


class AvgPrecisionAt(RankingMetric):
    def __init__(self, top_ks=None, labels_onehot=False):
        super(AvgPrecisionAt, self).__init__(top_ks=top_ks, labels_onehot=labels_onehot)
        self.precision_at = PrecisionAt(top_ks)._metric

    def _metric(self, ks: torch.Tensor, scores: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        """ Compute average precision at K for provided cutoffs """
        ks, scores, labels = check_inputs(ks, scores, labels)
        topk_scores, _, topk_labels = extract_topk(ks, scores, labels)
        avg_precisions = create_output_placeholder(scores, ks)

        # Compute average precisions at K
        num_relevant = torch.sum(labels, dim=1)
        max_k = ks.max().item()

        precisions = self.precision_at(1+torch.arange(max_k), topk_scores, topk_labels)
        rel_precisions = precisions * topk_labels

        for index, k in enumerate(ks):
            total_prec = rel_precisions[:, : int(k)].sum(dim=1)
            avg_precisions[:, index] = total_prec / num_relevant.clamp(min=1, max=k).to(dtype=torch.float32,
                                                                                        device=scores.device)  
            # Ensuring type is double, because it can be float if --fp16
        return avg_precisions

class DCGAt(RankingMetric):
    def __init__(self, top_ks=None, labels_onehot=False):
        super(DCGAt, self).__init__(top_ks=top_ks, labels_onehot=labels_onehot)

    def _metric(self, ks: torch.Tensor, scores: torch.Tensor, labels: torch.Tensor, log_base: int=2) -> torch.Tensor:
        """ Compute discounted cumulative gain at K for provided cutoffs (ignoring ties) """
        ks, scores, labels = check_inputs(ks, scores, labels)
        topk_scores, topk_indices, topk_labels = extract_topk(ks, scores, labels)
        dcgs = create_output_placeholder(scores, ks)

        # Compute discounts
        discount_positions = torch.arange(ks.max().item()).to(device=scores.device,
                                                              dtype=torch.float32)
        discount_log_base = torch.log(torch.Tensor([log_base]).to(device=scores.device,
                                                                  dtype=torch.float32)).item()
        discounts = 1 / (torch.log(discount_positions + 2) / discount_log_base)

        # Compute DCGs at K
        for index, k in enumerate(ks):
            dcgs[:, index] = torch.sum(
                (topk_labels[:, :k] * discounts[:k].repeat(topk_labels.shape[0], 1)), dim=1
            ).to(dtype=torch.float32, device=scores.device)  # Ensuring type is double, because it can be float if --fp16
        return dcgs


class NDCGAt(RankingMetric):
    def __init__(self, top_ks=None, labels_onehot=False):
        super(NDCGAt, self).__init__(top_ks=top_ks, labels_onehot=labels_onehot)
        self.dcg_at = DCGAt(top_ks)._metric

    def _metric(self, ks: torch.Tensor, scores: torch.Tensor, labels: torch.Tensor, log_base: int = 2) -> torch.Tensor:
        """ Compute normalized discounted cumulative gain at K for provided cutoffs (ignoring ties) """
        ks, scores, labels = check_inputs(ks, scores, labels)
        topk_scores, topk_indices, topk_labels = extract_topk(ks, scores, labels)
        # ndcgs = _create_output_placeholder(scores, ks) #TODO track if this line is needed

        # Compute discounted cumulative gains
        gains            = self.dcg_at(ks, topk_scores, topk_labels)
        gains_normalized = self.dcg_at(ks, topk_labels, topk_labels)

        # Prevent divisions by zero
        relevant_pos   = (gains_normalized != 0).nonzero(as_tuple=True)
        irrelevant_pos = (gains_normalized == 0).nonzero(as_tuple=True)

        gains[irrelevant_pos] = 0
        gains[  relevant_pos] /= gains_normalized[relevant_pos]
        return gains

class NextItemPredictionBlock(nn.Module):
    """
    Predict the interacted item-id probabilities.
    - During inference, the task consists of predicting the next item.
    - During training, the class supports the following Language modeling tasks:
        Causal LM, Masked LM, Permutation LM and Replacement Token Detection
        
    Parameters:
    -----------
    input_size: int
        Input size of this module.
    target_dim: int
        Dimension of the target.
    weight_tying: bool
        The embedding table weights are shared with the prediction network layer.
    embedding_table: torch.nn.Module
        Module that's used to store the embedding table for the item.
    softmax_temperature: float
        Softmax temperature, used to reduce model overconfidence, so that softmax(logits / T).
        Value 1.0 is equivalent to regular softmax.
    """

    def __init__(self, input_size: int,
                       target_dim: int,
                     weight_tying: bool = False,
                  embedding_table: Optional[nn.Module] = None,
              softmax_temperature: float = 0.):
        super().__init__()
        self.input_size = input_size
        self.target_dim = target_dim
        self.weight_tying = weight_tying
        self.embedding_table = embedding_table
        self.softmax_temperature = softmax_temperature
        self.activation = nn.LogSoftmax(dim=-1)

        if self.weight_tying:
            self.output_layer_bias = nn.Parameter(torch.Tensor(self.target_dim))
            torch.nn.init.zeros_(self.output_layer_bias)
        else:
            self.output_layer = nn.Linear(self.input_size[-1], self.target_dim)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if self.weight_tying:
            logits = F.linear(inputs,
                              weight=self.embedding_table.weight, 
                              bias=self.output_layer_bias,)
        else:
            logits = self.output_layer(inputs)

        if self.softmax_temperature > 0:
            # Softmax temperature to reduce model overconfidence
            logits = torch.div(logits, self.softmax_temperature)

        predictions = self.activation(logits)
        return predictions

    def _get_name(self) -> str:
        return "NextItemPredictionTask"

class NextItemPredictionTask(nn.Module):
    """
    Next-item prediction task.
    
    Parameters
    ----------
    loss: torch.nn.Module
        Loss function to use. Defaults to NLLLos.
    metrics: Iterable[torchmetrics.Metric]
        List of ranking metrics to use for evaluation.
    task_block:
        Module to transform input tensor before computing predictions.
    task_name: str, optional
        Name of the prediction task, if not provided a name will be automatically constructed based
        on the target-name & class-name.
    weight_tying: bool
        The embedding table weights are shared with the prediction layer.
    softmax_temperature: float
        Softmax temperature, to reduce model overconfidence --> softmax(logits / Temp)
        Value 1.0 is equivalent to regular softmax.
    padding_idx: int
        pad token id.
    target_dim: int
        vocabulary size of item ids
    """

    DEFAULT_METRICS = (
        # default metrics suppose labels are int encoded
                NDCGAt(top_ks=[10, 20], labels_onehot=True),
              RecallAt(top_ks=[10, 20], labels_onehot=True),
        AvgPrecisionAt(top_ks=[10, 20], labels_onehot=True),
    )

    def __init__(self, loss: nn.Module = nn.NLLLoss(ignore_index=0),
                    metrics: Iterable[tm.Metric] = DEFAULT_METRICS,
                 task_block: Optional[nn.Module] = None,
                  task_name: str = "next-item",
               weight_tying: bool = False,
        softmax_temperature: float = 1.,
                padding_idx: int = 0,
                 target_dim: int = None,):
        super(NextItemPredictionTask, self).__init__()
        self.loss = loss
        self.metrics = metrics
        self.task_name = task_name
        self.task_block = task_block
        self.softmax_temperature = softmax_temperature
        self.embedding_table = None
        self.weight_tying = weight_tying
        self.padding_idx = padding_idx
        self.target_dim = target_dim
        self.masking = None

    def build(self, input_size, masking=None, device=None, 
                    embedding_block=None, task_block=None, predict_block=None):
        if not len(input_size) == 3 or isinstance(input_size, dict):
            raise ValueError("NextItemPredictionTask needs a 3-D tensor as input, found:" f"{input_size}")
        self.device = device

        # Retrieve the embedding module to get the name of item id col and its related table
        self.task_block = task_block
        self.embedding_block = embedding_block
        if not self.target_dim:
            self.target_dim = self.embedding_block.num_embeddings
        if self.weight_tying:
            self.item_embedding = self.embedding_block
            item_dim = self.item_embedding.weight.shape[1]
            if input_size[-1] != item_dim and not task_block:
                self.task_block = nn.Linear(in_features=input_size[-1], 
                                           out_features=item_dim)

        # Retrieve the masking if used in the model block
        self.masking = masking
        if self.masking:
            self.padding_idx = self.masking.padding_idx

        self.predict_block = NextItemPredictionBlock(input_size=input_size[-1], 
                                                     target_dim=self.target_dim,
                                                   weight_tying=self.weight_tying,
                                                embedding_table=self.item_embedding,
                                            softmax_temperature=self.softmax_temperature)
    def forward(self, inputs: torch.Tensor, **kwargs):
        if isinstance(inputs, (tuple, list)):
            inputs = inputs[0]
        x = inputs.float()
        print(1, x.shape)

        if self.task_block:
            x = self.task_block(x)
        print(2, x.shape)

        # Retrieve labels from masking
        labels = self.masking.masked_targets
        print(0, labels.shape)

        # remove padded items
        target_flat = labels.flatten()
        non_pad_mask = target_flat != self.padding_idx
        labels_all = torch.masked_select(target_flat, non_pad_mask)
        print(0, labels_all.shape)
        x = self.remove_pad_3d(x, non_pad_mask)
        print(3, x.shape)

        # Compute predictions probs
        x = self.predict_block(x) 
        print(4, x.shape)

        return x

    def remove_pad_3d(self, inp_tensor, non_pad_mask):
        # inp_tensor: (n_batch x seq_len x emb_dim)
        inp_tensor = inp_tensor.flatten(end_dim=1)
        inp_tensor_fl = torch.masked_select(inp_tensor, non_pad_mask.unsqueeze(1).expand_as(inp_tensor))
        out_tensor = inp_tensor_fl.view(-1, inp_tensor.size(1))
        return out_tensor

    def calculate_metrics(self, predictions, targets, mode="val", forward=True, **kwargs) -> Dict[str, torch.Tensor]:
        if isinstance(targets, dict) and self.target_name:
            targets = targets[self.target_name]

        outputs = {}
        if forward:
            predictions = self(predictions)
        predictions = self.forward_to_prediction_fn(predictions)

        for metric in self.metrics:
            outputs[self.metric_name(metric)] = metric(predictions, targets)

        return outputs

    def compute_metrics(self):
        metrics = {self.metric_name(metric): metric.compute()
                   for metric in self.metrics
                   if getattr(metric, "top_ks", None)}
        
        # Explode metrics for each cut-off
        topks = {self.metric_name(metric): metric.top_ks for metric in self.metrics}
        results = {}
        for name, metric in metrics.items():
            for measure, k in zip(metric, topks[name]):
                results[f"{name}_{k}"] = measure
        return

prediction_head = NextItemPredictionTask(weight_tying=True, 
                                              metrics=[NDCGAt(top_ks=[10, 20], labels_onehot=True),  
                                                     RecallAt(top_ks=[10, 20], labels_onehot=True),])
prediction_head.build(input_size=list(features_attentioned.shape),
                         masking=sequence_mask, 
                 embedding_block=feature_processor.embedding['item_ids'])
prediction_head

predictions = prediction_head(features_attentioned)
predictions

predictions.shape