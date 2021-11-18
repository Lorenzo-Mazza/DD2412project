import WRN
import tensorflow as tf
from utils import *



AUTO = tf.data.AUTOTUNE
BATCH_SIZE = 512

def load_CIFAR_10(M):

    (x_train, y_train), (x_test, y_test) = tf.keras.datasets.cifar10.load_data()

    batch_repetition=1
    main_shuffle = tf.random.shuffle(tf.tile(tf.range(BATCH_SIZE), [batch_repetition]))
    to_shuffle = tf.shape(main_shuffle)[0]
    shuffle_indices = [
        tf.concat([tf.random.shuffle(main_shuffle[:to_shuffle]),
                   main_shuffle[to_shuffle:]], axis=0)
        for _ in range(M)]

    # training on a few examples because it's too slow otherwise, you can remove the [] to train on the full dataset
    training_data = (tf.data.Dataset.from_tensor_slices((x_train, y_train))
                     .batch(BATCH_SIZE*M).prefetch(AUTO)
                     .map(lambda x,y:(tf.stack([tf.gather(x, indices, axis=0)
                                                for indices in shuffle_indices], axis=1),
                                      tf.stack([tf.gather(y, indices, axis=0)
                                                for indices in shuffle_indices], axis=1)),
                          num_parallel_calls=AUTO, ))

    test_data = (tf.data.Dataset.from_tensor_slices((x_test, y_test))
                 .shuffle(BATCH_SIZE * 100000)
                 .batch(BATCH_SIZE)
                 .prefetch(AUTO))
    classes = tf.unique(tf.reshape(y_train, shape=(-1,)))[0].get_shape().as_list()[0]
    training_size = x_train.shape[0]
    input_dim = training_data.element_spec[0].shape[1:]
    return training_data, test_data, classes,training_size,input_dim


def train(tr_dataset, model, optimizer,metrics):
    iteratorX = iter(tr_dataset)
    while True:
        try:
            # get the next batch
            batchX = next(iteratorX)
            images = batchX[0]
            #print(tf.shape(images))
            labels = tf.squeeze(tf.one_hot(batchX[1], 10))
            with tf.GradientTape() as tape:
                logits = model(images, training=True)
                # print("Train logits:", logits)
                # print(batchX[1])
                # print("Train labels:", labels)
                negative_log_likelihood = tf.reduce_mean(tf.reduce_sum(
                    tf.keras.losses.categorical_crossentropy(
                        labels, logits, from_logits=True), axis=1))
                filtered_variables = []
                # tv= model.trainable_variables
                for var in model.trainable_variables:
                    if ('kernel' in var.name or 'batch_norm' in var.name or
                            'bias' in var.name):
                        filtered_variables.append(tf.reshape(var, (-1,)))
                l2_loss = l2_reg * 2 * tf.nn.l2_loss(tf.concat(filtered_variables, axis=0))
                # tf.nn returns l2 loss divided by 0.5 so we need to double it
                loss = l2_loss + negative_log_likelihood
            grads = tape.gradient(loss, model.trainable_variables)
            optimizer.apply_gradients(zip(grads, model.trainable_variables))
            probabilities = tf.nn.softmax(tf.reshape(logits, [-1, classes]))

            # Train metrics
            metrics['train/ece'].update_state(tf.argmax(tf.reshape(labels, [-1,classes]), axis=-1)
                                              , probabilities)
            metrics['train/loss'].update_state(loss)
            metrics['train/negative_log_likelihood'].update_state(negative_log_likelihood)
            metrics['train/accuracy'].update_state(tf.reshape(labels, [-1]), probabilities)

        except StopIteration:
            # if StopIteration is raised, break from loop
            # print(loss)
            break

def compute_test_metrics(model, test_data, test_metrics, M):
    iteratorX = iter(test_data)

    while True:
        try:
            # get the next batch
            batchX = next(iteratorX)
            images = tf.stack( # Batch
                        [tf.stack( # Input repetition
                            [batchX[0][i] for _ in range(M)]
                        ) for i in range(BATCH_SIZE)])

            logits=model(images)

            labels = tf.squeeze(tf.one_hot(batchX[1], 10))
            labels = tf.stack( # Batch
                        [tf.stack( # Input repetition
                            [labels[i] for _ in range(M)]
                        ) for i in range(BATCH_SIZE)])
            probabilities =tf.nn.softmax(tf.reshape(logits, [-1, classes]))

            negative_log_likelihood = tf.reduce_mean(tf.reduce_sum(
                                    tf.keras.losses.categorical_crossentropy(
                                    labels, logits, from_logits=True), axis=1))

            test_metrics['test/ece'].update_state(tf.argmax(tf.reshape(labels, [-1,classes]), axis=-1)
                                              , probabilities)
            # test_ metrics['test/loss'].update_state(loss)
            test_metrics['test/negative_log_likelihood'].update_state(negative_log_likelihood)
            test_metrics['test/accuracy'].update_state(tf.reshape(labels, [-1]), probabilities)
        except StopIteration:
            break

# Number of subnetworks (baseline=3)
M = 3

tr_data, test_data, classes, train_dataset_size,input_shape= load_CIFAR_10(M)
# WRN params
n, k = 28, 10


lr_decay_ratio = 0.1
base_lr = 0.1
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

model = WRN.build_model(input_shape, classes, n, k, M)
print(model.summary())
for epoch in range(0, EPOCHS):
    train(tr_data,model,optimizer, training_metrics)
    print("Epoch: {}".format(epoch))
    for name, metric in training_metrics.items():
        print("{} : {}".format(name,metric.result().numpy()))
        metric.reset_states()
    compute_test_metrics(model, test_data, test_metrics, M)
    for name, metric in test_metrics.items():
        print("{} : {}".format(name,metric.result().numpy()))
        metric.reset_states()

model.save_weights('./checkpoints/my_checkpoint')
