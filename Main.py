import WRN
import tensorflow as tf
from utils import *
import os
import pickle
import time
import matplotlib.pyplot as plt

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

AUTO = tf.data.AUTOTUNE
BATCH_SIZE = 256  # 512
RUN_ID = '0003'
SECTION = 'Cifar10'
PARENT_FOLDER = os.getcwd()
RUN_FOLDER = 'run/{}/'.format(SECTION)
RUN_FOLDER += '_'.join(RUN_ID)
if not os.path.exists(RUN_FOLDER):
    os.makedirs(RUN_FOLDER)
    os.mkdir(os.path.join(RUN_FOLDER, 'weights'))
    os.mkdir(os.path.join(RUN_FOLDER, 'metrics'))

physical_devices = tf.config.list_physical_devices('GPU')
for device in physical_devices:
    tf.config.experimental.set_memory_growth(device, True)

def train(tr_dataset, model, optimizer, metrics, num_labels):
    iteratorX = iter(tr_dataset)
    while True:
        try:
            # get the next batch
            batchX = next(iteratorX)
            images = batchX['image']
            labels= batchX['label']
            BATCH_SIZE = tf.shape(images)[0]
            main_shuffle = tf.random.shuffle(tf.tile(
                tf.range(BATCH_SIZE), [batch_repetitions]))
            to_shuffle = tf.cast(tf.cast(tf.shape(main_shuffle)[0], tf.float32),tf.int32)
            shuffle_indices = [
                tf.concat([tf.random.shuffle(main_shuffle[:to_shuffle]),
                           main_shuffle[to_shuffle:]], axis=0)
                for _ in range(M)]
            images = tf.stack([tf.gather(images, indices, axis=0)
                               for indices in shuffle_indices], axis=1)
            labels = tf.stack([tf.gather(labels, indices, axis=0)
                               for indices in shuffle_indices], axis=1)
            labels = tf.one_hot(labels, num_labels)

            with tf.GradientTape() as tape:
                logits = model(images, training=True)
                negative_log_likelihood = tf.reduce_mean(tf.reduce_sum(
                    tf.keras.losses.categorical_crossentropy(
                        labels, logits, from_logits=True), axis=1))
                filtered_variables = []
                for var in model.trainable_variables:
                    if ('kernel' in var.name or 'batch_norm' in var.name or
                            'bias' in var.name):
                        filtered_variables.append(tf.reshape(var, (-1,)))
                l2_loss = l2_reg * 2 * tf.nn.l2_loss(tf.concat(filtered_variables, axis=0))
                # tf.nn returns l2 loss divided by 0.5 so we need to double it
                loss = l2_loss + negative_log_likelihood

            grads = tape.gradient(loss, model.trainable_variables)
            optimizer.apply_gradients(zip(grads, model.trainable_variables))
            probabilities = tf.nn.softmax(tf.reshape(logits, [-1, num_labels]))
            flat_labels = tf.reshape(labels, [-1])
            metrics['train/ece'].update_state(tf.argmax(tf.reshape(labels, [-1, num_labels]), axis=-1)
                                              , probabilities)
            metrics['train/loss'].update_state(loss)
            metrics['train/negative_log_likelihood'].update_state(negative_log_likelihood)
            metrics['train/accuracy'].update_state(flat_labels, probabilities)

        except (StopIteration, tf.errors.OutOfRangeError):
            # if StopIteration is raised, break from loop
            # print("end of dataset")
            break



# Number of subnetworks (baseline=3)
M = 3
batch_repetitions = 1
train_batch_size = int(BATCH_SIZE / batch_repetitions)
test_batch_size = int(BATCH_SIZE)

# loading function parameters: 'cifar10','cifar100','imagenet' (for now)

tr_data, test_data, num_labels, train_dataset_size, test_dataset_size, input_shape = load_dataset('cifar10', train_batch_size, test_batch_size)
# WRN params
n, k = 28, 10

lr_decay_ratio = 0.2
base_lr = 0.1 * BATCH_SIZE / batch_repetitions / 128
lr_warmup_epochs = 1
lr_decay_epochs = [80, 160, 180]

EPOCHS = 250
l2_reg = 3e-4

steps_per_epoch = train_dataset_size // BATCH_SIZE
lr_schedule = WarmUpPiecewiseConstantSchedule(
    steps_per_epoch,
    base_lr,
    decay_ratio=lr_decay_ratio,
    decay_epochs=lr_decay_epochs,
    warmup_epochs=lr_warmup_epochs)
optimizer = tf.keras.optimizers.SGD(
    lr_schedule, momentum=0.9, nesterov=True)

training_metrics = {
    'train/negative_log_likelihood': tf.keras.metrics.Mean(),
    'train/accuracy': tf.keras.metrics.CategoricalAccuracy(),
    'train/loss': tf.keras.metrics.Mean(),
    'train/ece': ExpectedCalibrationError(),
}

test_metrics = {
    'test/negative_log_likelihood': tf.keras.metrics.Mean(),
    'test/accuracy': tf.keras.metrics.CategoricalAccuracy(),
    'test/ece': ExpectedCalibrationError(),
}

model = WRN.build_model(input_dims=[M] +input_shape,
                        output_dim=num_labels,
                        n=n,
                        k=k,
                        M=M)
tensorboard_callback = tf.keras.callbacks.TensorBoard(log_dir=os.path.join(RUN_FOLDER, 'metrics/logs')
                                                      , update_freq='epoch')
tensorboard_callback.set_model(model)
print(model.summary())
train_metrics_evolution = []
test_metrics_evolution = []

for epoch in range(0, EPOCHS):
    print("Epoch: {}".format(epoch))
    t1 = time.time()
    train(tr_data, model, optimizer, training_metrics,num_labels)
    t2 = time.time()
    if (epoch + 1) % 50 == 0:
        model.save_weights(os.path.join(RUN_FOLDER, 'weights/weights_%d.h5' % epoch))
    train_metric = {}
    for name, metric in training_metrics.items():
        train_metric[name] = metric.result().numpy()
        print("{} : {}".format(name, metric.result().numpy()))
        metric.reset_states()
    train_metrics_evolution.append(train_metric)
    t3 = time.time()
    compute_test_metrics(model, test_data, test_metrics, M, num_labels)
    t4 = time.time()
    test_metric = {}
    for name, metric in test_metrics.items():
        test_metric[name] = metric.result().numpy()
        print("{} : {}".format(name, metric.result().numpy()))
        metric.reset_states()
    test_metrics_evolution.append(test_metric)
    print(f"Epoch took {t4 - t1}s. Trainging took {t2 - t1}s and testing {t4 - t3}s\n")

model.save_weights(os.path.join(RUN_FOLDER, 'weights/final_weights.h5'))
metrics_evo = (train_metrics_evolution, test_metrics_evolution)
with open(os.path.join(RUN_FOLDER, 'metrics/metrics_evo.pickle'), 'wb') as f:
    pickle.dump(metrics_evo, f)

metric = "negative_log_likelihood"
metric_evo_train = []
metric_evo_test = []
with (open(os.path.join(RUN_FOLDER, 'metrics/metrics_evo.pickle'), "rb")) as f:
        metrics_train, metrics_test = pickle.load(f)

epochs = [i for i in range(len(metrics_train))]

for metric_train, metric_test in zip(metrics_train, metrics_test):
    metric_evo_train.append(metric_train["train/"+metric])
    metric_evo_test.append(metric_test["test/"+metric])

plt.plot(epochs, metric_evo_train)
plt.plot(epochs, metric_evo_test)
plt.title("Evolution of "+metric+" during training")
plt.show()
