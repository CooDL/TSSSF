#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import time
import logging
import pickle as pkl

import numpy as np
import tensorflow as tf

from lib import models
from lib import optimizers
from lib import rnn_cells

from configurable import Configurable
from vocab import Vocab
from dataset import Dataset

#-------------- Logging  ----------------#
program = os.path.basename(sys.argv[0])
L = logging.getLogger(program)
logging.basicConfig(format='%(asctime)s: %(levelname)s: %(message)s')
logging.root.setLevel(level=logging.INFO)
L.info("Running %s" % ' '.join(sys.argv))
MAX_TO_KEEP=10000

# TODO make the optimizer class inherit from Configurable
# TODO bayesian hyperparameter optimization
# TODO start a UD tagger/parser pipeline
#***************************************************************
class Network(Configurable):
  """"""
  
  #=============================================================
  def __init__(self, model, *args, **kwargs):
    """"""
    
    if args:
      if len(args) > 1:
        raise TypeError('Parser takes at most one argument')
    
    kwargs['name'] = kwargs.pop('name', model.__name__)
    super(Network, self).__init__(*args, **kwargs)
    if not os.path.isdir(self.save_dir):
      os.mkdir(self.save_dir)
    with open(os.path.join(self.save_dir, 'config.cfg'), 'w') as f:
      self._config.write(f)
      
    print(args)
    tf.assign_sub(self.global_step, 25000, use_locking=True)
    self._global_step = tf.Variable(0., trainable=False)
    self._global_epoch = tf.Variable(0., trainable=False)
    self._model = model(self._config, global_step=self.global_step)
    self._train_steps = 0
    L.info("Load emb = %s"%self.load_emb)
    L.info("Stacking = %s"%self.stack)
    if self.stack:
      L.info("Stack emb File = %s"%self.embed_file_stack)
      L.info("Stack emb Size = %s"%self.stack_embed_size)
      L.info("Stack LSTM Size = %s"%self.stack_recur_size)
      L.info("Stack LSTM Layer = %s"%self.stack_n_recur)
      L.info("Stack MLP Size = %s"%self.stack_mlp_size)

    self._vocabs = []
    vocab_files = [(self.word_file, 0, 'Words'),
                   (self.tag_file, 1, 'Tags')]
    for i, (vocab_file, index, name) in enumerate(vocab_files):
      vocab = Vocab(vocab_file, index, self._config,
                    name=name,
                    cased=self.cased if not i else True,
                    load_embed_file=(not i) and self.load_emb,
                    global_step=self.global_step)
      self._vocabs.append(vocab)
    
    self._trainset = Dataset(self.train_file, self._vocabs, model, self._config, name='Trainset')
    self._validset = Dataset(self.valid_file, self._vocabs, model, self._config, name='Validset')
    self._testset = Dataset(self.test_file, self._vocabs, model, self._config, name='Testset')
    
    self._ops = self._gen_ops()
    self.history = {
      'train_loss': [],
      'train_accuracy': [],
      'valid_loss': [],
      'valid_accuracy': [],
      'test_acuracy': 0
    }
    return
  
  #=============================================================
  def train_minibatches(self):
    """"""
    
    return self._trainset.get_minibatches(self.train_batch_size,
                                          self.model.input_idxs,
                                          self.model.target_idxs)
  
  #=============================================================
  def valid_minibatches(self):
    """"""
    
    return self._validset.get_minibatches(self.test_batch_size,
                                          self.model.input_idxs,
                                          self.model.target_idxs,
                                          shuffle=False)
  
  #=============================================================
  def test_minibatches(self):
    """"""
    
    return self._testset.get_minibatches(self.test_batch_size,
                                          self.model.input_idxs,
                                          self.model.target_idxs,
                                          shuffle=False)
  
  #=============================================================
  # assumes the sess has already been initialized
  def pretrain(self, sess):
    """"""
    
    saver = tf.train.Saver(name=self.name, max_to_keep=1)
    
    print_every = self.print_every
    pretrain_iters = self.pretrain_iters
    try:
      pretrain_time = 0
      pretrain_loss = 0
      pretrain_recur_loss = 0
      pretrain_covar_loss = 0
      pretrain_ortho_loss = 0
      n_pretrain_sents = 0
      n_pretrain_iters = 0
      total_pretrain_iters = 0
      while total_pretrain_iters < pretrain_iters:
        for j, (feed_dict, sents) in enumerate(self.train_minibatches()):
          inputs = feed_dict[self._trainset.inputs]
          targets = feed_dict[self._trainset.targets]
          start_time = time.time()
          _, loss, recur_loss, covar_loss, ortho_loss = sess.run(self.ops['pretrain_op'], feed_dict=feed_dict)
          pretrain_time += time.time() - start_time
          pretrain_loss += loss
          pretrain_recur_loss += recur_loss
          pretrain_covar_loss += covar_loss
          pretrain_ortho_loss += ortho_loss
          n_pretrain_sents += len(targets)
          n_pretrain_iters += 1
          total_pretrain_iters += 1
          if j % print_every == 0:
            pretrain_time = n_pretrain_sents / pretrain_time
            pretrain_loss /= n_pretrain_iters
            pretrain_recur_loss /= n_pretrain_iters
            pretrain_covar_loss /= n_pretrain_iters
            pretrain_ortho_loss /= n_pretrain_iters
            L.info('%6d) Pretrain loss: %.2e (%.2e + %.2e + %.2e)    Pretrain rate: %6.1f sents/sec' % (total_pretrain_iters, pretrain_loss, recur_loss, covar_loss, ortho_loss, pretrain_time))
            pretrain_time = 0
            pretrain_loss = 0
            pretrain_recur_loss = 0
            pretrain_covar_loss = 0
            pretrain_ortho_loss = 0
            n_pretrain_sents = 0
            n_pretrain_iters = 0
    except KeyboardInterrupt:
      # try:
      #   raw_input('\nPress <Enter> to save or <Ctrl-C> to exit')
      # except:
      #   L.info('\r', end='')
      #   sys.exit(0)
      saver.save(sess, os.path.join(self.save_dir, self.name.lower() + '-pretrained'), latest_filename=self.name.lower()+'-checkpoint')
    return
  
  #=============================================================
  # assumes the sess has already been initialized
  def train(self, sess):
    """"""
    
    save_path = os.path.join(self.save_dir, self.name.lower() + '-pretrained')
    saver = tf.train.Saver(name=self.name, max_to_keep=MAX_TO_KEEP)
    
    n_bkts = self.n_bkts
    train_iters = self.train_iters
    print_every = self.print_every
    validate_every = self.validate_every
    save_every = self.save_every

    try:

      train_time = 1
      train_loss = 0
      n_train_sents = 0
      n_train_correct = 0
      n_train_tokens = 1
      n_train_iters = 1
      total_train_iters = sess.run(self.global_step)
      valid_time = 0
      valid_loss = 0
      valid_accuracy = 0
      epoch_finished = 0
      while total_train_iters < train_iters:

        for j, (feed_dict, _) in enumerate(self.train_minibatches()):
          if print_every and total_train_iters % print_every == 0 and total_train_iters > 0:
            
            train_loss /= n_train_iters
            train_accuracy = 100 * n_train_correct / n_train_tokens
            train_time = n_train_sents / train_time
            L.info('Number of mini-batches trained: === %d ===\n\tTrain loss: %.4f    Train acc: %5.2f%%    Train rate: %6.1f sents/sec\n\tValid loss: %.4f    Valid acc: %5.2f%%    Valid rate: %6.1f sents/sec' % (total_train_iters, train_loss, train_accuracy, train_time, valid_loss, valid_accuracy, valid_time))
            L.info('@@@@@@@@@@@@@@@@@@@@@@@@@@Valitate Accuracy@@@@@@@@@@@@@@@@@@@@@@:\n%5.2f%%' % self.test(sess, validate=True,test_epoch=epoch_finished))
            L.info('##########################Test Accuracy #########################:\n%5.2f%%' % self.test(sess, validate=False,test_epoch=epoch_finished))

            train_time = 0
            train_loss = 0
            n_train_sents = 0
            n_train_correct = 0
            n_train_tokens = 0
            n_train_iters = 0
          train_inputs = feed_dict[self._trainset.inputs]
          train_targets = feed_dict[self._trainset.targets]
          start_time = time.time()

          _, loss, accuracy, n_correct, n_tokens = sess.run(self.ops['train_op'], feed_dict=feed_dict)

          train_time += time.time() - start_time
          train_loss += loss
          n_train_sents += len(train_targets)
          n_train_correct += n_correct
          n_train_tokens += n_tokens
          n_train_iters += 1
          total_train_iters += 1
          self.history['train_loss'].append(loss)
          self.history['train_accuracy'].append(100 * n_correct / n_tokens)

          if total_train_iters == 1 or total_train_iters % validate_every == 0:
            L.info("Validating and performing sanity check ...")
            valid_time = 0
            valid_loss = 0
            n_valid_sents = 0
            n_valid_correct = 0
            n_valid_tokens = 0
#
            with open(os.path.join(self.save_dir, 'sanitycheck.txt'), 'w') as f:
              for k, (feed_dict, _) in enumerate(self.valid_minibatches()):
                inputs = feed_dict[self._validset.inputs]
                targets = feed_dict[self._validset.targets]
                start_time = time.time()
                loss, n_correct, n_tokens,accuracy = sess.run(self.ops['valid_op'], feed_dict=feed_dict)
                valid_time += time.time() - start_time
                valid_loss += loss
                n_valid_sents += len(targets)
                n_valid_correct += n_correct
                n_valid_tokens += n_tokens
 #       #        self.model.sanity_check(inputs, targets, predictions, self._vocabs, f)

            valid_loss /= k+1
            valid_accuracy = 100 * n_valid_correct / n_valid_tokens
            valid_time = n_valid_sents / valid_time
            self.history['valid_loss'].append(valid_loss)
            self.history['valid_accuracy'].append(valid_accuracy)

        sess.run(self._global_epoch.assign_add(1.))
        train_loss_epoch = train_loss/n_train_iters
        train_accuracy = 100 * n_train_correct / n_train_tokens
        train_time = n_train_sents / train_time
        epoch_finished = sess.run(self._global_epoch)
        L.info('=== Finshed %d Epochs === \n\tTrain loss: %.4f    Train acc: %5.2f%%    Train rate: %6.1f sents/sec\n\tValid loss: %.4f    Valid acc: %5.2f%%    Valid rate: %6.1f sents/sec' % (epoch_finished, train_loss_epoch, train_accuracy, train_time, valid_loss, valid_accuracy, valid_time))
        L.info("Global_step = %s"%sess.run(self._global_step))

        L.info('Saving model after === %d === epochs to --> %s' % (epoch_finished, os.path.join(self.save_dir, self.name.lower() + '-trained-' + str(epoch_finished))))
        saver.save(sess, os.path.join(self.save_dir, self.name.lower() + '-trained'), latest_filename=self.name.lower(), global_step=self.global_epoch)
        
        with open(os.path.join(self.save_dir, 'history.pkl'), 'w') as f:
          pkl.dump(self.history, f)

    except KeyboardInterrupt:
      saver.save(sess, os.path.join(self.save_dir, self.name.lower() + '-trained'), latest_filename=self.name.lower(), global_step=self.global_epoch)

    with open(os.path.join(self.save_dir, 'history.pkl'), 'w') as f:
      pkl.dump(self.history, f)

#    self.test(sess, validate=True, test_epoch=epoch_finished)
#    self.test(sess, validate=False,test_epoch=epoch_finished)

    return
    
  #=============================================================
  # TODO make this work if lines_per_buff isn't set to 0
  def test(self, sess, validate=False, test_epoch=0):
    """"""
    
    if validate:
      filename = self.valid_file +'-epoch%d'%test_epoch
      minibatches = self.valid_minibatches
      dataset = self._validset
      op = self.ops['test_op'][0]
      score_file = 'scores_validate.txt'
    else:
      filename = self.test_file +'-epoch%d'%test_epoch
      minibatches = self.test_minibatches
      dataset = self._testset
      op = self.ops['test_op'][1]
      score_file = 'scores_test.txt'
    
    all_predictions = [[]]
    all_sents = [[]]
    bkt_idx = 0
    for (feed_dict, sents) in minibatches():
      mb_inputs = feed_dict[dataset.inputs]
      mb_targets = feed_dict[dataset.targets]
      mb_probs, mb_accuracy = sess.run(op, feed_dict=feed_dict)
      all_predictions[-1].extend(self.model.validate(mb_inputs, mb_targets, mb_probs))
      all_sents[-1].extend(sents)
      if len(all_predictions[-1]) == len(dataset[bkt_idx]):
        bkt_idx += 1
        if bkt_idx < len(dataset._metabucket):
          all_predictions.append([])
          all_sents.append([])
 #   with open(os.path.join(self.save_dir, os.path.basename(filename)), 'w') as f:
 #     for bkt_idx, idx in dataset._metabucket.data:
 #       data = dataset._metabucket[bkt_idx].data[idx][1:]
 #       preds = all_predictions[bkt_idx][idx]
 #       words = all_sents[bkt_idx][idx]
 #       for i, (datum, word, pred) in enumerate(zip(data, words, preds)):
 #         if self.load_emb:
 #           if self.stack:
 #             tup = (
 #               i+1,
 #               word,
 #               self.tags[pred[5]] if pred[5] != -1 else self.tags[datum[4]],
 #               self.tags[pred[6]] if pred[6] != -1 else self.tags[datum[5]],
 #               str(pred[7]) if pred[7] != -1 else str(datum[6]),
 #               self.rels[pred[8]] if pred[8] != -1 else self.rels[datum[7]],
 #               str(pred[9]) if pred[9] != -1 else '_',
 #               self.rels[pred[10]] if pred[10] != -1 else '_',
 #             )
 #           else:
 #             tup = (
 #               i+1,
 #               word,
 #               self.tags[pred[3]] if pred[3] != -1 else self.tags[datum[2]],
 #               self.tags[pred[4]] if pred[4] != -1 else self.tags[datum[3]],
 #               str(pred[5]) if pred[5] != -1 else str(datum[4]),
 #               self.rels[pred[6]] if pred[6] != -1 else self.rels[datum[5]],
 #               str(pred[7]) if pred[7] != -1 else '_',
 #               self.rels[pred[8]] if pred[8] != -1 else '_',
 #             )
 #         else:
 #           tup = (
 #             i+1,
 #             word,
 #             self.tags[pred[2]] if pred[2] != -1 else self.tags[datum[1]],
 #             self.tags[pred[3]] if pred[3] != -1 else self.tags[datum[2]],
 #             str(pred[4]) if pred[4] != -1 else str(datum[3]),
 #             self.rels[pred[5]] if pred[5] != -1 else self.rels[datum[4]],
 #             str(pred[6]) if pred[6] != -1 else '_',
 #             self.rels[pred[7]] if pred[7] != -1 else '_',
 #           )
 #         f.write('%s\t%s\t_\t%s\t%s\t_\t%s\t%s\t%s\t%s\n' % tup)
 #       f.write('\n')
 #   with open(os.path.join(self.save_dir, score_file), 'a+') as f:
 #     s, _ = self.model.evaluate(os.path.join(self.save_dir, os.path.basename(filename)), punct=self.model.PUNCT)
 #     f.write('Epoch%4d : '%test_epoch + s)
    return mb_accuracy
  
  #=============================================================
  def _gen_ops(self):
    """"""
    
    optimizer = optimizers.RadamOptimizer(self._config, global_step=self.global_step)
    train_output = self._model(self._trainset)
    
    l2_loss = self.l2_reg * tf.add_n([tf.nn.l2_loss(matrix) for matrix in tf.get_collection('Weights')]) if self.l2_reg else self.model.ZERO
    recur_loss = self.recur_reg * tf.add_n(tf.get_collection('recur_losses')) if self.recur_reg else self.model.ZERO
    covar_loss = self.covar_reg * tf.add_n(tf.get_collection('covar_losses')) if self.covar_reg else self.model.ZERO
    ortho_loss = self.ortho_reg * tf.add_n(tf.get_collection('ortho_losses')) if self.ortho_reg else self.model.ZERO
    regularization_loss = recur_loss + covar_loss + ortho_loss
    if self.recur_reg or self.covar_reg or self.ortho_reg or 'pretrain_loss' in train_output:
      optimizer2 = optimizers.RadamOptimizer(self._config)
      pretrain_loss = train_output.get('pretrain_loss', self.model.ZERO)
      pretrain_op = optimizer2.minimize(pretrain_loss+regularization_loss)
    else:
      pretrain_loss = self.model.ZERO
      pretrain_op = self.model.ZERO

    atscope_p = filter(lambda x: u'Trainable_base' not in x.name and u'RNN0' not in x.name and u'RNN1' not in x.name and u'RNN2' not in x.name and u'RNN3' not in x.name and u'MLP' not in x.name, tf.trainable_variables())
    atscope = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope= 'Attention_based')
    atoptimizer = tf.train.AdamOptimizer(learning_rate=0.001)
    atrainop= atoptimizer.minimize(train_output['loss'], var_list= atscope_p)  
    mdtrainop = optimizer.minimize(train_output['loss'], varlist = atscope_p)

    names_to_vars_trainable = {v.op.name: v for v in tf.trainable_variables()}
    print('####################names_to_vars_trainable#############################################')
    print(names_to_vars_trainable.keys())
    print('#########################filter scope varlist#############################')
    atops = {v.op.name: v for v in atscope_p}
    print(atops.keys())
    train_op = optimizer.minimize(train_output['loss']+l2_loss+regularization_loss,varlist=atscope)


    # These have to happen after optimizer.minimize is called
    valid_output = self._model(self._validset, moving_params=optimizer)
    test_output = self._model(self._testset, moving_params=optimizer)
    
    ops = {}
    ops['pretrain_op'] = [pretrain_op,
                          pretrain_loss,
                          recur_loss,
                          covar_loss,
                          ortho_loss]
    ops['train_op'] = [atrainop,
                       train_output['loss']+l2_loss+regularization_loss,
                       train_output['accuracy'],
                       train_output['n_correct'],
                       train_output['batch_size']]
    ops['valid_op'] = [valid_output['loss'],
                       valid_output['n_correct'],
                       valid_output['batch_size'],
                       valid_output['predictions']]
    ops['test_op'] = [[valid_output['probabilities'],valid_output['accuracy']],
                      [test_output['probabilities'], test_output['accuracy']]]
    ops['optimizer'] = optimizer

    return ops
    
  #=============================================================
  @property
  def global_step(self):
    return self._global_step
  @property
  def train_step(self):
    return self._train_step
  @property
  def global_epoch(self):
    return self._global_epoch
  @property
  def model(self):
    return self._model
  @property
  def words(self):
    return self._vocabs[0]
  @property
  def tags(self):
    return self._vocabs[1]
  @property
  def ops(self):
    return self._ops
  
#***************************************************************
if __name__ == '__main__':
  """"""
  
  import argparse
  
  argparser = argparse.ArgumentParser()
  argparser.add_argument('--pretrain', action='store_true')
  argparser.add_argument('--test', action='store_true')
  argparser.add_argument('--load', action='store_true')
  argparser.add_argument('--model', default='Parser')
  argparser.add_argument('--load_epoch', default=0, help="Epoch for test/load, ignore if use lastest checkpoint")
  args, extra_args = argparser.parse_known_args()
  cargs = {k: v for (k, v) in vars(Configurable.argparser.parse_args(extra_args)).iteritems() if v is not None}

  print(cargs)
  L.info('*** '+args.model+' ***')
  model = getattr(models, args.model)
  
  if 'save_dir' in cargs and os.path.isdir(cargs['save_dir']) and not (args.test or args.load):
    L.info('[!!!---ALERT---!!!] Save directory already exists. Overwriting directory %s'%cargs['save_dir'])
  if (args.test or args.load) and 'save_dir' in cargs:
    L.info('[!!!---ALERT---!!!] Config file may already exist. Overwriting config in directory %s'%cargs['save_dir'])
    # cargs['config_file'] = os.path.join(cargs['save_dir'], 'config.cfg')
  network = Network(model, **cargs)
  config_proto = tf.ConfigProto()#log_device_placement=True)
#  config_proto.gpu_options.per_process_gpu_memory_fraction = network.per_process_gpu_memory_fraction
  config_proto.gpu_options.allow_growth = True

  with tf.Session(config=config_proto) as sess:
    
    sess.run(tf.global_variables_initializer())

    if args.load_epoch == 0:
      pre_trained_model = tf.train.latest_checkpoint(network.save_dir, latest_filename=network.name.lower())
    else:
      pre_trained_model = os.path.join(network.save_dir, network.name.lower() + '-trained-' + str(args.load_epoch))
    names_to_vars = {v.op.name: v for v in tf.all_variables()}
    print('***********************************************************')
    print(names_to_vars)
    if args.pretrain:
      network.pretrain(sess)
    if not args.test:
      if args.load:
#        print("*************************************************")
        print(network.save_dir)
        print(network.name.lower())
        print(args.load_epoch)
#        print('******************************************tf.GraphKeys.TRAINABLE_VARIABLES***********************************')
#        print(tf.GraphKeys.TRAINABLE_VARIABLES)
#        print("#############################################tf.trainable_variables()#######################################")
#        print(tf.trainable_variables())
        os.system('echo Loading: > %s/HEAD' % network.save_dir)
        os.system('git rev-parse HEAD >> %s/HEAD' % network.save_dir)
        L.info('Loading parameters to bottom LSTM from model : %s'%pre_trained_model)
        save_vars = filter(lambda x: u'RNN4' not in x.name and u'Tags' not in x.name and u'Words' not in x.name and u'beta1_power' not in x.name and u'beta2_power' not in x.name and u'RNN5' not in x.name and u'Attention_based' not in x.name and u'Pretrained' not in x.name and u'Trainable_stack' not in x.name and u'Pretrained_stack' not in x.name, tf.global_variables())
#        print('**********************save_vars*************************')
#        print(save_vars)        
        saver = tf.train.Saver(name=network.name, var_list=save_vars)
        saver.restore(sess, pre_trained_model)
        L.info('Loaded parameters to bottom LSTM from model : %s'%pre_trained_model)
        network._global_step=0
        if os.path.isfile(os.path.join(network.save_dir, 'history.pkl')):
          with open(os.path.join(network.save_dir, 'history.pkl')) as f:
            network.history = pkl.load(f)
      else:
        os.system('echo Training: >> %s/HEAD' % network.save_dir)
        os.system('git rev-parse HEAD >> %s/HEAD' % network.save_dir)
      network.train(sess)
    else:
      os.system('echo Testing: >> %s/HEAD' % network.save_dir)
      os.system('git rev-parse HEAD >> %s/HEAD' % network.save_dir)
      saver = tf.train.Saver(name=network.name)
      pre_trained_model = os.path.join(network.save_dir, network.name.lower() + '-trained-' + str(args.load_epoch))
      saver.restore(sess, pre_trained_model)
      network.test(sess, validate=False,test_epoch=int(args.load_epoch))
