'''
A TensorFlow V2 implementation of the Liquid Time-Stochasticity cell proposed
by Raneez and Wirasingha (2023) at https://doi.org/10.1109/CCWC57344.2023.10099071

An RNN with continuous-time hidden
states determined by stochastic differential equations
'''

import tensorflow as tf
import numpy as np
from enum import Enum

class MappingType(Enum):
	Affine = 0

class SDESolver(Enum):
	EulerMaruyama = 0

class NoiseType(Enum):
	diagonal = 0

class LTSCell(tf.keras.layers.Layer):

	def __init__(self, units, **kwargs):
		'''
		Initializes the LTS cell & parameters
		Calls parent Layer constructor to initialize required fields
		Variables adapted from ltc.py
		'''

		super(LTSCell, self).__init__(**kwargs)
		self.input_size = -1
		self.units = units
		self.built = False

		self._time_step = 1.0
		self._brownian_motion = None

		# Number of SDE solver steps in one RNN step
		self._sde_solver_unfolds = 6
		self._solver = SDESolver.EulerMaruyama
		self._noise_type = NoiseType.diagonal

		self._input_mapping = MappingType.Affine

		self._erev_init_factor = 1

		self._w_init_max = 1.0
		self._w_init_min = 0.01
		self._cm_init_min = 0.5
		self._cm_init_max = 0.5
		self._gleak_init_min = 1
		self._gleak_init_max = 1

		self._w_min_value = 0.00001
		self._w_max_value = 1000
		self._gleak_min_value = 0.00001
		self._gleak_max_value = 1000
		self._cm_t_min_value = 0.000001
		self._cm_t_max_value = 1000

		self._fix_cm = None
		self._fix_gleak = None
		self._fix_vleak = None

		self._input_weights = None
		self._input_biases = None

	@property
	def state_size(self):
		return self.units

	def build(self, input_shape):
		'''
		Automatically triggered the first time __call__ is run
		'''

		self.input_size = int(input_shape[-1])
		self._init_variables()
		self.built = True

	@tf.function
	def call(self, inputs, states):
		'''
		Automatically calls build() the first time.
		Runs the LTS cell for one step using the previous RNN cell output & state
		by calculating the SDE solver to generate the next output and state
		'''

		inputs = self._map_weights_and_biases(inputs)
		next_state = self._sde_solver_euler_maruyama(inputs, states)
		output = next_state
		return output, next_state

	def get_config(self):
		'''
		Enable serialization
		'''

		config = super(LTSCell, self).get_config()
		config.update({ 'units': self.units })
		return config

	### Helper methods ###
	def _init_variables(self):
		'''
		Creates the variables to be used within __call__
		'''

		# Define sensory variables
		self.sensory_mu = tf.Variable(
			tf.random.uniform(
				[self.input_size, self.units],
				minval = 0.3,
				maxval = 0.8,
				dtype = tf.float32
			),
			name = 'sensory_mu',
			trainable = True,
		)

		self.sensory_sigma = tf.Variable(
			tf.random.uniform(
				[self.input_size, self.units],
				minval = 3.0,
				maxval = 8.0,
				dtype = tf.float32
			),
			name = 'sensory_sigma',
			trainable = True,
		)

		self.sensory_W = tf.Variable(
			tf.constant(
				np.random.uniform(
					low = self._w_init_min,
					high = self._w_init_max,
					size = [self.input_size, self.units]
				),
				dtype = tf.float32
			),
			name = 'sensory_W',
			trainable = True,
			shape = [self.input_size, self.units]
		)

		sensory_erev_init = 2 * np.random.randint(
			low = 0,
			high = 2,
			size = [self.input_size, self.units]
		) - 1
		self.sensory_erev = tf.Variable(
			tf.constant(
				sensory_erev_init * self._erev_init_factor,
				dtype = tf.float32
			),
			name = 'sensory_erev',
			trainable = True,
			shape = [self.input_size, self.units]
		)

		# Define base stochastic differential equation variables
		self.mu = tf.Variable(
			tf.random.uniform(
				[self.units, self.units],
				minval = 0.3,
				maxval = 0.8,
				dtype = tf.float32
			),
			name = 'mu',
			trainable = True,
		)

		self.sigma = tf.Variable(
			tf.random.uniform(
				[self.units, self.units],
				minval = 3.0,
				maxval = 8.0,
				dtype = tf.float32
			),
			name = 'sigma',
			trainable = True,
		)

		self.W = tf.Variable(
			tf.constant(
				np.random.uniform(
					low = self._w_init_min,
					high = self._w_init_max,
					size = [self.units, self.units]
				),
				dtype = tf.float32
			),
			name = 'W',
			trainable = True,
			shape = [self.units, self.units]
		)

		erev_init = 2 * np.random.randint(
			low = 0,
			high = 2,
			size = [self.units, self.units]
		) - 1
		self.erev = tf.Variable(
			tf.constant(
				erev_init * self._erev_init_factor,
				dtype = tf.float32
			),
			name = 'erev',
			trainable = True,
			shape = [self.units, self.units]
		)

		# Define a simple Wiener process (Brownian motion)
		self._brownian_motion = tf.Variable(
			tf.random.normal(
				[self.units],
				mean = 0.0,
				stddev = tf.sqrt(self._time_step),
				dtype = tf.float32
			)
		)

		# Synaptic leakage conductance variables of the neural dynamics of small species
		if self._fix_vleak is None:
			self.vleak = tf.Variable(
				tf.random.uniform(
					[self.units],
					minval = -0.2,
					maxval = 0.2,
					dtype = tf.float32
				),
				name = 'vleak',
				trainable = True,
			)
		else:
			self.vleak = tf.Variable(
				tf.constant(self._fix_vleak, dtype = tf.float32),
				name = 'vleak',
				trainable = False,
				shape = [self.units]
			)

		if self._fix_gleak is None:
			initializer = tf.constant(self._gleak_init_min, dtype = tf.float32)

			if self._gleak_init_max > self._gleak_init_min:
				initializer = tf.random.uniform(
					[self.units],
					minval = self._gleak_init_min,
					maxval = self._gleak_init_max,
					dtype = tf.float32
				)

			self.gleak = tf.Variable(
				initializer,
				name = 'gleak',
				trainable = True,
			)
		else:
			self.gleak = tf.Variable(
				tf.constant(self._fix_gleak),
				name = 'gleak',
				trainable = False,
				shape = [self.units]
			)

		if self._fix_cm is None:
			initializer = tf.constant(self._cm_init_min, dtype = tf.float32)

			if self._cm_init_max > self._cm_init_min:
				initializer = tf.random.uniform(
					[self.units],
					minval = self._cm_init_min,
					maxval = self._cm_init_max,
					dtype = tf.float32
				)

			self.cm_t = tf.Variable(
				initializer,
				name = 'cm_t',
				trainable = True,
			)
		else:
			self.cm_t = tf.Variable(
				tf.constant(self._fix_cm),
				name = 'cm_t',
				trainable = False,
				shape = [self.units]
			)

	def _map_weights_and_biases(self, inputs):
		'''
		Initializes weights & biases to be used
		'''

		# Create a workaround from creating tf Variables every function call
		# init with None and set only if not None - aka only first time
		if self._input_weights is None:
			self._input_weights = tf.Variable(
				lambda: tf.ones(
					[self.input_size],
					dtype = tf.float32
				),
				name = 'input_weights',
				trainable = True
			)

		if self._input_biases is None:
			self._input_biases = tf.Variable(
				lambda: tf.zeros(
					[self.input_size],
					dtype = tf.float32
				),
				name = 'input_biases',
				trainable = True
			)

		inputs = inputs * self._input_weights
		inputs = inputs + self._input_biases

		return inputs

	@tf.function
	def _sde_solver_euler_maruyama(self, inputs, states):
		'''
		Implement Euler Maruyama implicit SDE solver
		'''

		for _ in range(self._sde_solver_unfolds):
			# Compute drift and diffusion terms
			drift = self._sde_solver_drift(inputs, states)
			diffusion = self._sde_solver_diffusion(inputs, states)

			# Compute the next state
			states = states + drift * self._time_step + diffusion * self._brownian_motion
			states = tf.reshape(states, shape=[int(self._time_step), self.units])

		return states

	@tf.function
	def _sde_solver_drift(self, inputs, states):
		'''
		Compute the drift term of the Euler-Maruyama SDE solver
		Implement custom Euler ODE solver - first-order numerical procedure
		Utilize the LTC's deterministic solver
		'''

		# State returned as -> tuple(Tensor); previous return state x(t), to produce x(t+1)
		v_pre = states[0]

		sensory_w_activation = self.sensory_W * self._sigmoid(inputs, self.sensory_mu, self.sensory_sigma)
		sensory_rev_activation = sensory_w_activation * self.sensory_erev

		w_numerator_sensory = tf.reduce_sum(input_tensor = sensory_rev_activation, axis = 1)
		w_denominator_sensory = tf.reduce_sum(input_tensor = sensory_w_activation, axis = 1)

		for _ in range(self._sde_solver_unfolds):
			w_activation = self.W * self._sigmoid(v_pre, self.mu, self.sigma)

			rev_activation = w_activation * self.erev

			w_numerator = tf.reduce_sum(input_tensor = rev_activation, axis = 1) + w_numerator_sensory
			w_denominator = tf.reduce_sum(input_tensor = w_activation, axis = 1) + w_denominator_sensory

			numerator = self.cm_t * v_pre + self.gleak * self.vleak + w_numerator
			denominator = self.cm_t + self.gleak + w_denominator

			v_pre = numerator / denominator

		return v_pre

	@tf.function
	def _sde_solver_diffusion(self, inputs, states):
		'''
		Compute the diffusion term of the Euler-Maruyama SDE solver
		'''

		return 1.0

	@tf.function
	def _sigmoid(self, v_pre, mu, sigma):
		v_pre = tf.reshape(v_pre, [-1, v_pre.shape[-1], 1])
		mues = v_pre - mu
		x = sigma * mues
		return tf.nn.sigmoid(x)

# References
# https://splunktool.com/how-can-i-implement-a-custom-rnn-specifically-an-esn-in-tensorflow
# https://colab.research.google.com/github/luckykadam/adder/blob/master/rnn_full_adder.ipynb
# https://www.tutorialexample.com/build-custom-rnn-by-inheriting-rnncell-in-tensorflow-tensorflow-tutorial/
# https://notebook.community/tensorflow/docs-l10n/site/en-snapshot/guide/migrate
# https://www.tensorflow.org/api_docs/python/tf/keras/layers/AbstractRNNCell
# https://www.tensorflow.org/guide/keras/custom_layers_and_models/#layers_are_recursively_composable
# https://www.tensorflow.org/guide/function#creating_tfvariables
# https://docs.sciml.ai/DiffEqDocs/stable/solvers/sde_solve/#Full-List-of-Methods
