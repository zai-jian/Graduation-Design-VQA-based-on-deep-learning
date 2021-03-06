import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

from torchvision import models, transforms
from torch.autograd import Variable

import os
import re
import glob
import pickle
import numpy as np
from PIL import Image

from DataLoader import *


## VGG16 (removed last layer) for feature extraction CNN除最后一层softmax
class _VGG16(nn.Module):
    def __init__(self, origin_model):
        super(_VGG16, self).__init__()
        self.feature = origin_model.features
        self.classifier = nn.Sequential(*list(origin_model.classifier.children())[:-1])

        for param in self.parameters():
            param.requires_gard = False

    def forward(self, x):
        f = self.feature(x)
        f = f.view(f.size(0), -1)
        y = self.classifier(f)
        return y


## The MLP baseline MLP层
class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.fc1 = nn.Linear(4096 + 300 + 300, 8192)
        self.dropout1 = nn.Dropout(0.5)
        self.fc2 = nn.Linear(8192, 1)

    def forward(self, x):
        x = x.view(-1, 4096 + 300 + 300)
        x = F.relu(self.dropout1(self.fc1(x)))
        y = F.sigmoid(self.fc2(x))
        return y


# read feature file 从图片的npy文件中提取dictionary，以文件名为key，以feature为value
def load_feature(path):
    dict = {}
    pattern = os.path.join(path, '*.npy')
    for i, filepath in enumerate(glob.glob(pattern), 1):
        feature_batch = np.load(filepath)
        for key in feature_batch.item():
            dict[key] = feature_batch.item()[key]
    print(len(dict))

    return dict


# feature extractor - save to disk 把图片转为feature
def get_vgg16_feature(image_dict, f_dict, batchsize):
    if not os.path.exists(f_dict):
        os.makedirs(f_dict)

    if torch.cuda.is_available():
        my_vgg = _VGG16(models.vgg16(pretrained=True)).cuda()
    else:
        my_vgg = _VGG16(models.vgg16(pretrained=True))

    # transforms.Compose(transforms)将多个操作组合成一个函数
    transform = transforms.Compose([
        transforms.ToTensor(),  # range [0, 255] -> [0.0,1.0]转为tensor
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        # 将图片各个维度normalize，为啥要normalize???????????????
    ])

    batch = []  # 一个batch中所有图片的集合
    filenames = []  # 一个batch中所有图片名字的集合
    dict = {}  # 一个batch中图片名对应feature
    batch_id = 1
    pattern = os.path.join(image_dict, '*jpg')
    for i, filepath in enumerate(glob.glob(pattern), 1):
        filenames.append(os.path.basename(filepath))
        im = Image.open(filepath).convert('RGB')  # 将图片转为RBG模式,为啥要转呢？？？？？？？？？？？？？？？？？？？
        im = transform(im)  # 转为tensor并normalize
        im.unsqueeze_(0)  # 加一个维度变4维，为了之后cat
        batch.append(im)  # batch为5维

        if i % batchsize == 0:
            batch = torch.cat(batch)  # batch变4维
            if torch.cuda.is_available():
                output = my_vgg(Variable(batch).cuda())
            else:
                output = my_vgg(Variable(batch))

            output = (output.data).cpu().numpy()  # put variable on cpu, transform Variable into numpy array
            for filename, feature in zip(filenames, output):
                dict[filename] = feature
            np.save(os.path.join(f_dict, 'dict_' + str(batch_id)),
                    dict)  # Save an array to a binary file in NumPy .npy format.
            batch_id += 1

            batch = []
            filenames = []
            dict = {}
            print(i / 47300 * 100, '%')

    if len(batch) != 0:
        batch = torch.cat(batch)  # batch变4维
        if torch.cuda.is_available():
            output = my_vgg(Variable(batch).cuda())
        else:
            output = my_vgg(Variable(batch))

        output = (output.data).cpu().numpy()  # put variable on cpu, transform Variable into numpy array
        for filename, feature in zip(filenames, output):
            dict[filename] = feature
        np.save(os.path.join(f_dict, 'dict_' + str(batch_id)),
                dict)  # Save an array to a binary file in NumPy .npy format.

    print('image to feature done')


# calculate the mean of embedding for a given string
def embedding_mean(str, emb_dict):
    str = str.lower()
    words = re.findall(r"[:]|[^\w\s]|\w+", str)

    mean = []
    for word in words:
        if word in emb_dict:
            mean.append(emb_dict[word])

    mean = np.asarray(mean)

    return np.mean(mean, axis=0)


# prepare training data 把question vector，question vector，answer vector作为X，将label作为Y，打包成batchx，batchy，再打包成mini_batches
# sample(filename, question, answer, split, label)
def prepare_minibatch(sample_set, f_dict, emb_dict, batchsize):
    minibatch = []
    batch_X = []
    batch_Y = []
    for i, sample in enumerate(sample_set, 1):
        imagename = sample['i']
        q = sample['q']
        a = sample['a']

        f = f_dict[imagename]
        emb_q = embedding_mean(q, emb_dict)
        emb_a = embedding_mean(a, emb_dict)

        x = np.concatenate([f, emb_q, emb_a])
        y = [int(sample['label'])]
        batch_X.append(x)
        batch_Y.append(y)

        if i % batchsize == 0:
            batch_X = np.asarray(batch_X)
            batch_Y = np.asarray(batch_Y)
            minibatch.append((batch_X, batch_Y))
            batch_X = []
            batch_Y = []

    if len(batch_X) != 0:
        batch_X = np.asarray(batch_X)
        batch_Y = np.asarray(batch_Y)
        minibatch.append((batch_X, batch_Y))

    return minibatch


def loss_acc(minibatch, net, criterion):
    loss = 0.0
    correct = 0
    num_samples = 0

    for i, batch in enumerate(minibatch, 1):
        X, Y = batch
        num = Y.shape[0]
        num_samples+=num
        if torch.cuda.is_available():
            X = Variable(torch.from_numpy(batch_x).float()).cuda()
            Y = Variable(torch.from_numpy(batch_y).float()).cuda()
        else:
            X = Variable(torch.from_numpy(batch_x).float())
            Y = Variable(torch.from_numpy(batch_y).float())

        y_hat = net(X)
        values, ids = torch.max(y_hat.data.view(-1, 4), 1)  # 共有4个答案，最后一个为正确答案，将model给出的答案index vector转为0/1 vector
        ids = ids[ids == 3]  # indices == 3返回一个index所在的值都为3的index vector
        if ids.size() != torch.Tensor([]).size():
            correct += ids.size()[0]
        loss += criterion(y_hat, Y).data[0] * num  # 为啥只用data[0], 而不是总和

    return loss / num_samples, correct / num_samples * 4


BATCH_SIZE = 32

# test
if __name__ == '__main__':
    ## load json
    data = json.load('../dataset/Visual7W/dataset_v7w_telling/dataset_v7w_telling.json', 'r')

    # construct dataset from json
    # 从data的json中提取出一个一个samplesample(filename, question, answer, split, label)
    # 其中相同的question的四个答案的sample连在一起，最后一个为正确答案
    dataset = construct_dataset(data)

    # split dataset into train, valid and test
    train_set, eval_set, test_set = split_dataset(dataset)
    print('load dataset done', len(train_set), len(eval_set), len(test_set))

    # parse glove
    # glove中每个word后面跟300个数字最为一个line，将所有embedding整理成字典
    path = '../dataset/GloVe/vectors.6B.300d.txt'
    emb_dict = parse_glove(path)
    print('load embedding done', len(emb_dict))

    # get dictionary from all the question and answer, dictionary is a set
    dictionary = get_dictionary(dataset)
    print(len(dictionary))
    print(dictionary)

    new_dict = {}
    for word in dictionary:
        if word in emb_dict:
            new_dict[word] = emb_dict[word]
    with open('../dataset/GloVe/300d_visual7w_dict.pkl', 'wb') as f:
        pickle.dump(new_dict, f, pickle.HIGHEST_PROTOCOL)
    print('write 300d_visual7w_dict.pkl done', len(new_dict))

    emb_dict = {}  # word - embedding dictionary
    with open('../dataset/GloVe/300d_visual7w_dict.pkl', 'rb') as f:
        emb_dict = pickle.load(f)
    print('load 300d_visual7w_dict.pkl done', len(emb_dict))

    # get offine images features
    get_vgg16_feature('../dataset/Visual7W/ResizeImages/', '../dataset/Visual7W/imageFeatureVGG16/', BATCH_SIZE)

    # load image feature
    f_dict = load_feature('../dataset/Visual7W/imageFeatureVGG16/')

    train_minibatch = prepare_minibatch(train_set, f_dict, emb_dict, BATCH_SIZE)
    eval_minibatch = prepare_minibatch(eval_set, f_dict, emb_dict, BATCH_SIZE)

    # build MLP model
    if torch.cuda.is_available():
        net = Net().cuda()
        criterion = nn.BCELoss.cuda()
    else:
        net = Net()
        criterion = nn.BCELoss()

    # optimizer = optim.SGD(net.parameters(), lr=0.01, momentum=0.9)
    optimizer = optim.Adam(net.parameters(), lr=1e-4, weight_decay=1e-4)

    epoches = 500
    for epoch in range(epoches):
        running_loss = 0.0
        for i, batch in enumerate(train_minibatch, 1):
            batch_x, batch_y = batch
            if torch.cuda.is_available():
                X = Variable(torch.from_numpy(batch_x).float()).cuda()
                Y = Variable(torch.from_numpy(batch_y).float()).cuda()
            else:
                X = Variable(torch.from_numpy(batch_x).float())
                Y = Variable(torch.from_numpy(batch_y).float())

            optimizer.zero_grad()

            y_hat = net(X)
            loss = criterion(y_hat, Y)
            loss.backward()
            optimizer.step()

            running_loss += loss.data[0]
            if i % 1000 == 0:  # print every 2000 mini-batches
                print('[%d, %5d] running_loss(train): %.3f' % (epoch + 1, i, running_loss / 1000))
                running_loss = 0.0

        net.eval()  # Sets the module in evaluation mode. This has any effect only on modules such as Dropout or BatchNorm.
        train_loss, train_acc = loss_acc(train_minibatch, net, criterion)
        eval_loss, eval_acc = loss_acc(eval_minibatch, net, criterion)
        print('[%d] train_loss: %.4f, valid_loss: %.4f, train_acc: %.4f, valid_acc: %.4f' % (
        epoch + 1, train_loss, eval_loss, train_acc, eval_acc))
        net.train()  # Sets the module in train mode.
